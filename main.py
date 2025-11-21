from datetime import datetime
import time
import random
import pytz
import yaml
import sys
from pathlib import Path
from collections import defaultdict
from typing import Dict, Any, Optional, Tuple

from playerokapi.account import Account
from playerokapi.types import (
    ItemStatuses,
    PriorityTypes,
    UserProfile,
    ItemPriorityStatus,
    ItemProfile,
)


def _hold_console_before_exit():
    """
    Не даем терминалу закрыться сразу — ждём, пока пользователь нажмет Enter.
    Полезно при запуске двойным кликом в Windows.
    """
    try:
        input("\nНажмите Enter, чтобы закрыть окно...")
    except EOFError:
        # На всякий случай задержка, если stdin недоступен
        time.sleep(5)


def safe_exit(code: int = 0):
    _hold_console_before_exit()
    sys.exit(code)


class Config:
    """Класс для работы с конфигурацией"""

    def __init__(self, config_path: str = "config.yaml"):
        self.config_path = Path(config_path)
        self.data = self._load_config()
        self._validate_config()

    def _load_config(self) -> Dict[str, Any]:
        """Загрузка конфигурации из YAML файла"""
        if not self.config_path.exists():
            self._create_default_config()
            print(f"⚠️  Создан файл конфигурации: {self.config_path}")
            print("📝 Пожалуйста, заполните его и перезапустите программу")
            safe_exit(0)

        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except Exception as e:
            print(f"❌ Ошибка чтения конфигурации: {e}")
            safe_exit(1)
        return {}

    def _create_default_config(self):
        """Создание конфигурации по умолчанию"""
        default_config = {
            "credentials": {
                "token": "your_token_here",
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "username": "your_username_here",
            },
            "intervals": {
                # Диапазон интервала между проверками (в минутах).
                # Если хотите фиксированное значение — используйте update_interval.
                "update_interval_min": 1800,
                "update_interval_max": 1800,
                # Старое поле (фиксированное значение, в минутах) — на случай совместимости:
                "update_interval": 30,
                # Порог продвижения (в минутах)
                "promo_threshold": 30,
            },
            "settings": {
                "verbose": True,
                "item_status": "APPROVED",
                # Диапазон задержек между продвижениями (в секундах, можно нецелые)
                "delay_between_promos_min": 1.2,
                "delay_between_promos_max": 3.5,
                # Старое поле (фиксированное значение, в секундах) — для совместимости:
                "delay_between_promos": 2,
                # Диапазон задержек между парсингом страниц
                "delay_between_pages_min": 0.0,
                "delay_between_pages_max": 0.0,
            },
            "filters": {
                # Если список не пуст — продвигать ТОЛЬКО эти имена (exclude игнорируется)
                "include_names": [],
                # Если include_names пуст — продвигать все, КРОМЕ этих имен
                "exclude_names": [],
                # Ограничение на число продвигаемых товаров с одинаковым именем за один цикл:
                # -1 или None — без ограничений; 1 — не более одного; 2 — не более двух и т.д.
                "duplicates_limit": -1,
            },
        }

        with open(self.config_path, "w", encoding="utf-8") as f:
            yaml.dump(
                default_config,
                f,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            )

    def _validate_config(self):
        """Проверка обязательных полей и корректности диапазонов"""
        required_fields = {
            "credentials.token": self.token,
            "credentials.username": self.username,
        }

        missing = [
            k
            for k, v in required_fields.items()
            if not v or v == "your_token_here" or v == "your_username_here"
        ]

        if missing:
            print("❌ Не заполнены обязательные поля в config.yaml:")
            for field in missing:
                print(f"   - {field}")
            safe_exit(1)

        if getattr(ItemStatuses, self.item_status, None) is None:
            print(
                f"❌ Поле item_status заполнено не верно! Доступные статусы: {', '.join(ItemStatuses._member_names_)}"
            )
            safe_exit(1)

        # Валидация диапазона задержек между промо
        dmin, dmax = self._delay_between_promos_bounds()
        if dmin <= 0 or dmax <= 0 or dmin > dmax:
            print(
                "❌ Некорректные значения delay_between_promos_min/max. "
                "Убедитесь, что > 0 и min ≤ max."
            )
            safe_exit(1)

        # Валидация диапазона интервала обновления, если он задан диапазоном
        umin, umax = self._update_interval_bounds_minmax()
        if umin is not None and umax is not None:
            if umin <= 0 or umax <= 0 or umin > umax:
                print(
                    "❌ Некорректные значения update_interval_min/max. "
                    "Убедитесь, что > 0 и min ≤ max (минуты)."
                )
                safe_exit(1)

        # Валидация фильтров имён
        def _ensure_str_list(value, field_name: str):
            if value is None:
                return []
            if isinstance(value, str):
                return [value]
            if isinstance(value, list):
                # Фильтруем не-строки, конвертируем к str по возможности
                out = []
                for x in value:
                    if isinstance(x, str):
                        out.append(x)
                    else:
                        try:
                            out.append(str(x))
                        except Exception:
                            print(f"❌ Некорректный элемент в {field_name}: {x!r}")
                            safe_exit(1)
                return out
            print(f"❌ Поле {field_name} должно быть строкой или списком строк.")
            safe_exit(1)

        # Приведение include/exclude к корректным спискам
        _ = _ensure_str_list(
            self.data.get("filters", {}).get("include_names"), "filters.include_names"
        )
        _ = _ensure_str_list(
            self.data.get("filters", {}).get("exclude_names"), "filters.exclude_names"
        )

        # Валидация duplicates_limit
        dl = self.data.get("filters", {}).get("duplicates_limit", -1)
        try:
            if dl is None:
                dl = -1
            dl = int(dl)
        except Exception:
            print(
                "❌ filters.duplicates_limit должен быть целым числом (или -1 для без ограничений)."
            )
            safe_exit(1)
        if dl < -1:
            print("❌ filters.duplicates_limit не может быть меньше -1.")
            safe_exit(1)

        # Предупреждение, если задан и include, и exclude
        if self.include_names and self.exclude_names:
            print(
                "ℹ️  Замечание: filters.include_names задан — filters.exclude_names будет проигнорирован."
            )

        # Валидация диапазона задержек между страницами
        pmin, pmax = self._delay_between_pages_bounds()
        if pmin < 0 or pmax < 0 or pmin > pmax:
            print(
                "❌ Некорректные значения delay_between_pages_min/max. "
                "Убедитесь, что ≥ 0 и min ≤ max (секунды)."
            )
            safe_exit(1)

    @property
    def token(self) -> str:
        return self.data.get("credentials", {}).get("token", "")

    @property
    def user_agent(self) -> str:
        return self.data.get("credentials", {}).get("user_agent", "")

    @property
    def username(self) -> str:
        return self.data.get("credentials", {}).get("username", "")

    # --- Интервалы проверок (минуты -> секунды) ---
    def _update_interval_bounds_minmax(self) -> Tuple[Optional[float], Optional[float]]:
        intervals = self.data.get("intervals", {})
        umin = intervals.get("update_interval_min")
        umax = intervals.get("update_interval_max")
        # Приводим к float, если заданы
        if umin is not None:
            umin = float(umin)
        if umax is not None:
            umax = float(umax)
        return umin, umax

    def next_update_interval_sec(self) -> float:
        """
        Возвращает следующий интервал ожидания до новой проверки (в секундах).
        Если задан диапазон (min/max в минутах) — возвращает случайное значение.
        Иначе — использует фиксированное значение update_interval (в минутах).
        """
        umin, umax = self._update_interval_bounds_minmax()
        if umin is not None and umax is not None:
            # Случайное число
            return random.uniform(umin, umax)
        # Фиксированное значение в минутах
        return float(self.data.get("intervals", {}).get("update_interval", 1800))

    def describe_update_interval(self) -> str:
        """Строка для отображения интервала в баннере/логах"""
        umin, umax = self._update_interval_bounds_minmax()
        if umin is not None and umax is not None:
            return f"{umin:g}–{umax:g} сек (случайно)"
        return f"{int(self.update_interval_sec // 60)} сек (фикс.)"

    def _delay_between_pages_bounds(self) -> Tuple[float, float]:
        intervals = self.data.get("intervals", {})
        pmin = intervals.get("delay_between_pages_min", 0)
        pmax = intervals.get("delay_between_pages_max", 0)
        if pmin is None:
            pmin = 0
        if pmax is None:
            pmax = 0
        return float(pmin), float(pmax)

    def random_delay_between_pages_sec(self) -> float:
        pmin, pmax = self._delay_between_pages_bounds()
        if pmin == 0 and pmax == 0:
            return 0.0
        return random.uniform(pmin, pmax)

    def describe_pages_delay(self) -> str:
        pmin, pmax = self._delay_between_pages_bounds()
        if pmin == 0 and pmax == 0:
            return "выключена"
        return f"{pmin:g}–{pmax:g} сек (случайно)"

    @property
    def update_interval_sec(self) -> int:
        """
        Сохраняем старое поведение: "фиксированный" интервал (минуты) -> секунды.
        Используется для обратной совместимости и отображения, когда диапазон не задан.
        """
        return int(
            float(self.data.get("intervals", {}).get("update_interval", 30)) * 60
        )

    @property
    def promo_threshold_sec(self) -> int:
        return int(
            float(self.data.get("intervals", {}).get("promo_threshold", 30)) * 60
        )

    @property
    def verbose(self) -> bool:
        return bool(self.data.get("settings", {}).get("verbose", True))

    @property
    def item_status(self) -> str:
        return str(self.data.get("settings", {}).get("item_status", "APPROVED"))

    # --- Задержка между продвижениями (секунды) ---
    def _delay_between_promos_bounds(self) -> Tuple[float, float]:
        intervals = self.data.get("intervals", {})
        # Новые поля (min/max) в секундах
        dmin = intervals.get("delay_between_promos_min")
        dmax = intervals.get("delay_between_promos_max")
        if dmin is not None and dmax is not None:
            return float(dmin), float(dmax)
        # Фолбэк к старому фиксированному значению
        fixed = float(intervals.get("delay_between_promos", 2))
        return fixed, fixed

    def random_delay_between_promos_sec(self) -> float:
        dmin, dmax = self._delay_between_promos_bounds()
        return random.uniform(dmin, dmax)

    # Оставляем для совместимости (не используется напрямую, если есть min/max)
    @property
    def delay_between_promos(self) -> int:
        return int(float(self.data.get("intervals", {}).get("delay_between_promos", 2)))

    @property
    def include_names(self) -> set[str]:
        names = self.data.get("filters", {}).get("include_names", [])
        if isinstance(names, str):
            names = [names]
        return {
            _normalize_name(n)
            for n in (names or [])
            if isinstance(n, str) and n.strip()
        }

    @property
    def exclude_names(self) -> set[str]:
        names = self.data.get("filters", {}).get("exclude_names", [])
        if isinstance(names, str):
            names = [names]
        return {
            _normalize_name(n)
            for n in (names or [])
            if isinstance(n, str) and n.strip()
        }

    @property
    def duplicates_limit(self) -> int:
        dl = self.data.get("filters", {}).get("duplicates_limit", -1)
        if dl is None:
            return -1
        try:
            return int(dl)
        except Exception:
            return -1


class RateLimiter:
    """Управление rate limiting и повторными попытками"""

    def __init__(self, config: Config):
        self.config = config
        self.consecutive_errors = 0
        self.last_error_time = None

    def calculate_wait_time(self, error_code: Optional[int] = None) -> float:
        """Расчет времени ожидания с экспоненциальной задержкой (для 429) и рандомизацией"""
        if error_code == 429:
            # Для 429 используем прогрессивную (экспоненциальную) задержку
            base_wait = 5  # базовое ожидание 5 секунд
            exponential_wait = base_wait * (2 ** min(self.consecutive_errors, 5))
            # Добавляем случайность (jitter) для распределения нагрузки
            jitter = random.randint(0, 10)
            return float(min(exponential_wait + jitter, 120))  # максимум 10 минут
        # Между промо — используем диапазон из конфига (может быть нецелым)
        return self.config.random_delay_between_promos_sec()

    def on_error(self):
        """Вызывается при ошибке rate limiting"""
        self.consecutive_errors += 1
        self.last_error_time = _get_now()

    def on_success(self):
        """Вызывается при успешном запросе"""
        self.consecutive_errors = 0
        self.last_error_time = None


def _get_now():
    """Получение текущего времени в UTC"""
    return datetime.now(pytz.utc)


def _normalize_name(name: Optional[str]) -> str:
    """Нормализация имени товара для сравнения (обрезка и без учета регистра)."""
    return (name or "").strip().lower()


def print_banner(config: Config):
    """Вывод информации о запуске"""
    # Короткое описание фильтров
    if config.include_names:
        filter_line = f"только: {len(config.include_names)} имен"
    elif config.exclude_names:
        filter_line = f"кроме: {len(config.exclude_names)} имен"
    else:
        filter_line = "нет"

    dup_line = (
        "без ограничений"
        if config.duplicates_limit == -1
        else str(config.duplicates_limit)
    )

    print(
        f"""
╔{"═" * 58}╗
║{" " * 23}PLAYEROK BOT{" " * 23}║
╚{"═" * 58}╝

📋 Конфигурация:
   • Пользователь: {config.username}
   • Интервал проверки: {config.describe_update_interval()}
   • Порог продвижения: {config.promo_threshold_sec // 60} мин
   • Подробный режим: {"Вкл" if config.verbose else "Выкл"}
   • Фильтры по именам: {filter_line}
   • Лимит дубликатов на имя (за цикл): {dup_line}
   • Пауза между страницами: {config.describe_pages_delay()}
""".lstrip()
    )


def main():
    # Загрузка конфигурации
    config = Config("config.yaml")

    print_banner(config)

    # Инициализация аккаунта
    try:
        acc = Account(
            token=config.token,
            user_agent=config.user_agent,
        ).get()
        user = acc.get_user(username=config.username)
        print("✅ Успешная авторизация\n")
    except Exception as e:
        print(f"❌ Ошибка авторизации: {e}")
        safe_exit(1)

    # Основной цикл
    while True:
        print(f"\n{'=' * 60}")
        print(
            f"[{_get_now().strftime('%Y-%m-%d %H:%M:%S UTC')}] Запуск проверки товаров"
        )
        print(f"{'=' * 60}")

        check_and_update_products(acc, user, config)

        # Случайная пауза до следующей проверки (если задан диапазон — он будет использован)
        sleep_seconds = config.next_update_interval_sec()
        if sleep_seconds >= 60:
            sleep_minutes = sleep_seconds / 60.0
            print(f"\n⏳ Ожидание {sleep_minutes:.1f} минут до следующей проверки...")
        else:
            print(f"\n⏳ Ожидание {sleep_seconds:.0f} секунд до следующей проверки...")
        time.sleep(sleep_seconds)


def promote_product_with_retry(
    acc: Account,
    product: ItemProfile,
    premium_priority: ItemPriorityStatus,
    is_published: bool,
    rate_limiter: RateLimiter,
    max_retries: int = 3,
) -> Tuple[bool, str]:
    """
    Продвижение товара с повторными попытками

    Returns:
        Tuple[bool, str]: (успех, сообщение)
    """
    # return True, f"Продвинут со статусом: ТЕСТ"
    for attempt in range(max_retries):
        try:
            if is_published:
                acc.increase_item_priority_status(
                    product.id,
                    premium_priority.id,
                    transaction_provider_id="LOCAL",  # type: ignore
                )
            else:
                acc.publish_item(product.id, premium_priority.id)
            rate_limiter.on_success()
            return True, f"Продвинут со статусом: {premium_priority.name}"

        except Exception as e:
            error_message = str(e)

            # Проверяем, является ли это ошибкой 429
            if "429" in error_message or "TOO_MANY_REQUESTS" in error_message:
                rate_limiter.on_error()
                wait_time = rate_limiter.calculate_wait_time(429)

                if attempt < max_retries - 1:
                    print(
                        f"   ⚠️  Достигнут rate limit (попытка {attempt + 1}/{max_retries})"
                    )
                    print(f"   ⏳ Ожидание {int(wait_time)} секунд...")

                    # Показываем прогресс-бар ожидания (шагами по 5 сек)
                    remaining_int = int(wait_time)
                    for remaining in range(remaining_int, 0, -5):
                        mins, secs = divmod(remaining, 60)
                        print(f"   ⌛ Осталось: {mins:02d}:{secs:02d}", end="\r")
                        time.sleep(min(5, remaining))

                    print()  # Новая строка после прогресс-бара
                    continue
                else:
                    return (
                        False,
                        f"Превышен лимит попыток после {max_retries} попыток. Пропуск товара.",
                    )

            # Другие ошибки
            else:
                return False, f"Ошибка при продвижении: {error_message}"

    return False, "Неизвестная ошибка"


def check_and_update_products(acc: Account, user: UserProfile, config: Config) -> None:
    """Проверка и обновление товаров с учетом фильтров и лимита дубликатов"""
    cursor = None
    total_checked = 0
    total_promoted = 0
    total_skipped = 0
    total_errors = 0

    # Счетчик успешно продвинутых товаров по нормализованному имени за этот цикл
    promoted_by_name = defaultdict(int)

    rate_limiter = RateLimiter(config)
    page_max_retries = 5  # Кол-во попыток получить страницу при 429

    try:
        while True:
            # --- Получаем страницу товаров с ретраями на 429 ---
            item_list = None
            for attempt in range(page_max_retries):
                try:
                    item_list = user.get_items(
                        after_cursor=cursor,
                        statuses=[getattr(ItemStatuses, config.item_status)],
                    )
                    rate_limiter.on_success()
                    break  # Успешно получили страницу
                except Exception as e:
                    error_message = str(e)
                    is_rate_limited = (
                        "429" in error_message
                        or "TOO_MANY_REQUESTS" in error_message
                        or "Too many attempts" in error_message
                    )

                    if is_rate_limited:
                        rate_limiter.on_error()
                        wait_time = rate_limiter.calculate_wait_time(429)

                        if attempt < page_max_retries - 1:
                            print(
                                f"   ⚠️  Достигнут rate limit при получении страницы (попытка {attempt + 1}/{page_max_retries})"
                            )
                            print(f"   ⏳ Ожидание {int(wait_time)} секунд...")

                            # Прогресс-бар ожидания (шаг 5 секунд)
                            remaining_int = int(wait_time)
                            for remaining in range(remaining_int, 0, -5):
                                mins, secs = divmod(remaining, 60)
                                print(
                                    f"   ⌛ Осталось: {mins:02d}:{secs:02d}", end="\r"
                                )
                                time.sleep(min(5, remaining))
                            print()  # новая строка
                            continue
                        else:
                            print(
                                f"   ❌ Превышен лимит попыток получения страницы после {page_max_retries} попыток."
                            )
                            total_errors += 1
                            # Завершаем текущий цикл проверки — верхний уровень подождёт и попробует снова
                            print(
                                f"\n📊 Итого: проверено {total_checked}, продвинуто {total_promoted} товаров"
                            )
                            return
                    else:
                        # Не 429 — не мучаем ретраями, выходим с ошибкой страницы
                        print(
                            f"   ❌ Ошибка при получении страницы товаров: {error_message}"
                        )
                        total_errors += 1
                        print(
                            f"\n📊 Итого: проверено {total_checked}, продвинуто {total_promoted} товаров"
                        )
                        return

            if item_list is None:
                # На всякий случай (не должно наступить)
                print("   ❌ Не удалось получить данные страницы товаров.")
                total_errors += 1
                print(
                    f"\n📊 Итого: проверено {total_checked}, продвинуто {total_promoted} товаров"
                )
                return

            # --- Обработка товаров на странице ---
            for product in item_list.items:
                total_checked += 1

                raw_name = product.name or ""
                norm_name = _normalize_name(raw_name)

                # 1) Фильтр по include/exclude
                if config.include_names:
                    if norm_name not in config.include_names:
                        if config.verbose:
                            print(f"⏭️  [{raw_name}] — пропуск (не в списке include)")
                        total_skipped += 1
                        continue
                else:
                    if config.exclude_names and norm_name in config.exclude_names:
                        if config.verbose:
                            print(f"⏭️  [{raw_name}] — пропуск (в списке exclude)")
                        total_skipped += 1
                        continue

                # 2) Порог времени публикации
                try:
                    approval_date = datetime.fromisoformat(product.approval_date)
                except Exception:
                    if config.verbose:
                        print(
                            f"⚠️  [{raw_name}] — некорректная дата публикации, пропуск"
                        )
                    total_skipped += 1
                    continue

                if approval_date.tzinfo is None:
                    approval_date = pytz.utc.localize(approval_date)

                times_passed_sec = (_get_now() - approval_date).total_seconds()
                times_passed_min = int(times_passed_sec // 60)

                if times_passed_sec <= config.promo_threshold_sec:
                    if config.verbose:
                        remaining_min = int(
                            (config.promo_threshold_sec - times_passed_sec) // 60
                        )
                        print(f"⏭️  [{raw_name}] - до продвижения {remaining_min} мин")
                    continue  # не считаем это как "скип по фильтру"

                # 3) Лимит дубликатов (по успешно продвинутым в этом цикле)
                if (
                    config.duplicates_limit >= 0
                    and promoted_by_name[norm_name] >= config.duplicates_limit
                ):
                    if config.verbose:
                        print(
                            f"⏭️  [{raw_name}] — достигнут лимит дублей {config.duplicates_limit} для этого имени"
                        )
                    total_skipped += 1
                    continue

                # 4) Получение статусов приоритета и продвижение
                if config.verbose:
                    print(f"\n📦 [{raw_name}] (ID: {product.id})")
                    print(f"   ⏱️  Прошло с публикации: {times_passed_min} мин")

                try:
                    priority_statuses = acc.get_item_priority_statuses(
                        product.id, str(product.price)
                    )
                    premium_priority = next(
                        (
                            status
                            for status in priority_statuses
                            if status.type == PriorityTypes.PREMIUM
                        ),
                        None,
                    )

                    if premium_priority:
                        success, message = promote_product_with_retry(
                            acc,
                            product,
                            premium_priority,
                            config.item_status == "APPROVED",
                            rate_limiter,
                            max_retries=3,
                        )

                        if success:
                            print(f"   ✅ {message}")
                            total_promoted += 1
                            promoted_by_name[norm_name] += 1

                            # Динамическая задержка между продвижениями
                            wait_time = rate_limiter.calculate_wait_time()
                            if wait_time > 0:
                                time.sleep(wait_time)
                        else:
                            print(f"   ❌ {message}")
                            if "Пропуск товара" in message:
                                total_skipped += 1
                            else:
                                total_errors += 1
                    else:
                        if config.verbose:
                            print("   ⚠️  Премиум статус недоступен")
                        total_skipped += 1

                except Exception as e:
                    print(f"   ❌ Неожиданная ошибка: {e}")
                    total_errors += 1

            if not item_list.page_info.has_next_page:
                break

            # Пауза между страницами (если включена)
            wait_time = config.random_delay_between_pages_sec()
            if wait_time > 0:
                time.sleep(wait_time)

            cursor = item_list.page_info.end_cursor

    except Exception as e:
        print(f"❌ Ошибка при получении списка товаров: {e}")

    print(f"\n📊 Итого: проверено {total_checked}, продвинуто {total_promoted} товаров")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n👋 Программа остановлена пользователем")
        safe_exit(0)
    except Exception as e:
        print(f"\n❌ Критическая ошибка: {e}")
        safe_exit(1)
