"""Generate an algorithm explanation PDF for DeliveryIQ."""
from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# ── Fonts (Cyrillic-capable) ────────────────────────────────────────────────
WIN_FONTS = Path("C:/Windows/Fonts")
pdfmetrics.registerFont(TTFont("Body", str(WIN_FONTS / "arial.ttf")))
pdfmetrics.registerFont(TTFont("Body-Bold", str(WIN_FONTS / "arialbd.ttf")))
pdfmetrics.registerFont(TTFont("Body-Italic", str(WIN_FONTS / "ariali.ttf")))
pdfmetrics.registerFont(TTFont("Mono", str(WIN_FONTS / "consola.ttf")))

from reportlab.pdfbase.pdfmetrics import registerFontFamily

registerFontFamily(
    "Body", normal="Body", bold="Body-Bold", italic="Body-Italic",
)

# ── Styles ──────────────────────────────────────────────────────────────────
ACCENT = HexColor("#1f4e79")
SOFT = HexColor("#f4f7fb")
BORDER = HexColor("#cbd5e1")

styles = getSampleStyleSheet()
title_style = ParagraphStyle(
    "Title", parent=styles["Title"], fontName="Body-Bold",
    fontSize=22, leading=28, textColor=ACCENT, alignment=TA_LEFT,
    spaceAfter=14,
)
h1_style = ParagraphStyle(
    "H1", parent=styles["Heading1"], fontName="Body-Bold",
    fontSize=16, leading=20, textColor=ACCENT, spaceBefore=14, spaceAfter=8,
)
h2_style = ParagraphStyle(
    "H2", parent=styles["Heading2"], fontName="Body-Bold",
    fontSize=13, leading=17, textColor=HexColor("#2c5282"),
    spaceBefore=10, spaceAfter=4,
)
body_style = ParagraphStyle(
    "Body", parent=styles["BodyText"], fontName="Body",
    fontSize=11, leading=15, alignment=TA_JUSTIFY, spaceAfter=6,
)
bullet_style = ParagraphStyle(
    "Bullet", parent=body_style, leftIndent=14, bulletIndent=2, spaceAfter=3,
)
mono_style = ParagraphStyle(
    "Mono", parent=body_style, fontName="Mono", fontSize=9.5, leading=12,
    textColor=HexColor("#1a365d"), backColor=SOFT, borderColor=BORDER,
    borderWidth=0.5, borderPadding=6, leftIndent=4, rightIndent=4,
    spaceBefore=4, spaceAfter=8,
)
note_style = ParagraphStyle(
    "Note", parent=body_style, fontName="Body-Italic", fontSize=10.5,
    textColor=HexColor("#475569"), leftIndent=10,
    backColor=HexColor("#fffbeb"), borderColor=HexColor("#facc15"),
    borderWidth=0.5, borderPadding=6, spaceBefore=4, spaceAfter=8,
)

# ── Helpers ─────────────────────────────────────────────────────────────────
def P(text, style=body_style):
    return Paragraph(text, style)


def H1(text):
    return P(text, h1_style)


def H2(text):
    return P(text, h2_style)


def bullets(items):
    return [Paragraph(f"• {it}", bullet_style) for it in items]


def code(text):
    safe = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    safe = safe.replace("\n", "<br/>")
    return Paragraph(safe, mono_style)


def note(text):
    return Paragraph(text, note_style)


def table(rows, col_widths):
    t = Table(rows, colWidths=col_widths, hAlign="LEFT")
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "Body"),
        ("FONTNAME", (0, 0), (-1, 0), "Body-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
        ("TEXTCOLOR", (0, 0), (-1, 0), HexColor("#ffffff")),
        ("BACKGROUND", (0, 1), (-1, -1), SOFT),
        ("GRID", (0, 0), (-1, -1), 0.4, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return t


# ── Document ────────────────────────────────────────────────────────────────
OUT = Path(__file__).with_name("DeliveryIQ_Algorithm.pdf")

doc = SimpleDocTemplate(
    str(OUT), pagesize=A4,
    leftMargin=2.0 * cm, rightMargin=2.0 * cm,
    topMargin=1.8 * cm, bottomMargin=1.8 * cm,
    title="Принцип роботи алгоритму оптимізації — DeliveryIQ",
    author="Tymur Nikolaiev",
)

story = []

# ── Title page ─────────────────────────────────────────────────────────────
story += [
    P("Принцип роботи алгоритму оптимізації", title_style),
    P("DeliveryIQ — оптимізація мультимодальних маршрутів доставки",
      ParagraphStyle("Sub", parent=body_style, fontSize=13,
                     textColor=HexColor("#475569"), spaceAfter=20)),
    P(
        "Цей документ покроково описує, як застосунок перетворює адресу депо "
        "та список точок доставки на оптимізований маршрут (TSP) або план "
        "розвезення флотом (CVRPTW). Кожен крок прив'язаний до конкретного "
        "модуля у вихідному коді.",
    ),
]

# ── Overview ───────────────────────────────────────────────────────────────
story += [
    H1("1. Загальний потік даних"),
    P(
        "Конвеєр складається з шести логічних етапів. Кожен етап має чітко "
        "окреслений вхід і вихід; проміжні дані кешуються у "
        "<b>st.session_state</b>, щоб уникнути повторних завантажень мережі "
        "OSM та повторних обчислень матриці."
    ),
    Spacer(1, 4),
    table(
        [
            ["Етап", "Модуль", "Результат"],
            ["1. Геокодування", "geocoder.py", "(lat, lon) для кожної адреси"],
            ["2. Завантаження мережі", "graph_builder.py",
             "MultiDiGraph OSM (LSCC-зрізаний)"],
            ["3. Прив'язка до вузлів", "graph_builder.py",
             "node_id для депо та точок"],
            ["4. Часові мітки ребер", "graph_builder.py",
             "{drive, bike, walk} — три графи"],
            ["5a. TSP (1 ТЗ)", "route_solver.py",
             "Замкнений маршрут + час"],
            ["5b. VRP (флот)", "vrp_solver.py",
             "Маршрути по ТЗ + попередження"],
            ["6. Візуалізація", "visualizer.py",
             "Folium-карта з AntPath"],
        ],
        col_widths=[3.6 * cm, 4.2 * cm, 8.8 * cm],
    ),
    Spacer(1, 6),
    note(
        "Архітектурне рішення: для оптимізації мережа завантажується через "
        "<b>cached_network_at(lat, lon, radius)</b>, а не за назвою міста. "
        "Інакше Nominatim може поставити мітку міста далеко від району "
        "доставки і всі адреси прив'яжуться до одного граничного вузла."
    ),
]

# ── Step 2: graph ──────────────────────────────────────────────────────────
story += [
    H1("2. Підготовка дорожньої мережі"),
    H2("2.1. Завантаження OSM"),
    P(
        "Через бібліотеку OSMnx завантажується граф із "
        "<b>network_type=\"all\"</b> — щоб одразу мати ребра пішохідних "
        "доріжок, велодоріжок і проїздів. Перед завантаженням викликається "
        "<b>_configure_osmnx_tags()</b>, щоб залишити модальні теги "
        "(<i>bicycle</i>, <i>motor_vehicle</i>, <i>cycleway:*</i> тощо) на "
        "ребрах — ці теги пізніше визначатимуть прохідність."
    ),
    H2("2.2. Зрізання до LSCC"),
    P(
        "Сирий граф може містити невеликі ізольовані компоненти "
        "(відрізані дороги, помилки OSM). Виклик "
        "<b>ox.truncate.largest_component(G_raw, strongly=True)</b> залишає "
        "лише найбільшу сильно зв'язну компоненту. Це гарантує, що від "
        "будь-якого вузла можна доїхати до будь-якого іншого — критично "
        "для подальшого Дейкстри."
    ),
    note(
        "Прив'язка координат до вузлів виконується ВИКЛЮЧНО після зрізання, "
        "бо інакше можна отримати node_id, якого немає у фінальному графі — "
        "build_distance_matrix() тоді кине RuntimeError."
    ),
    H2("2.3. Часові мітки + три модальні копії"),
    P(
        "Функція <b>add_travel_times(G)</b> створює три глибокі копії "
        "графа і для кожного режиму проставляє атрибут "
        "<b>travel_time</b> (секунди) на кожному ребрі. Непрохідні для "
        "режиму ребра отримують сентинельне значення "
        "<b>PENALTY = 1e9 s</b> — достатньо велике, щоб домінувати над "
        "будь-яким реальним шляхом, але скінченне, щоб TSP-розв'язувачі "
        "могли формально завершити маршрут."
    ),
    Spacer(1, 4),
    table(
        [
            ["Режим", "Швидкість", "Що блокується (PENALTY)"],
            ["drive", "30 км/год",
             "footway, pedestrian, steps, motor_vehicle=no/private"],
            ["bike", "15 км/год",
             "steps, bicycle=no/dismount, односторонній рух проти напрямку"],
            ["walk", "5 км/год (steps × 0.5)",
             "foot=no, access=private"],
        ],
        col_widths=[2.2 * cm, 3.6 * cm, 10.8 * cm],
    ),
]

story += [PageBreak()]

# ── Step 5a: TSP ───────────────────────────────────────────────────────────
story += [
    H1("3. Оптимізація TSP (один транспортний засіб)"),
    P(
        "Завдання комівояжера: знайти такий замкнений порядок відвідання "
        "точок <b>π = (depot, π₁, π₂, …, πₙ, depot)</b>, який мінімізує "
        "сумарний час подорожі"
    ),
    code("Cost(π) = Σ matrix[π[i] → π[i+1]]   для i = 0 … n"),
    H2("3.1. Матриця відстаней (Фаза 1)"),
    P(
        "Функція <b>build_distance_matrix(G, nodes)</b> застосовує "
        "алгоритм Дейкстри для кожної пари (src, dst) і повертає словник "
        "<b>{(src, dst): travel_time_seconds}</b>. Перед розрахунком вузли "
        "дедуплікуються через <i>dict.fromkeys()</i>: дві адреси можуть "
        "прив'язатися до одного OSM-вузла, і без дедуплікації TSP отримав "
        "би 0-вартісні ребра й нульовий сумарний час."
    ),
    P("Якщо шляху немає (NetworkXNoPath), у клітинку записується PENALTY."),
    H2("3.2. Гібридна 'остання миля' для drive"),
    P(
        "Багато адрес (під'їзди, дворики) недоступні для авто. Замість "
        "відкидати їх, застосунок паркує авто у "
        "<b>nearest_car_accessible_node()</b> — найближчому вузлі, "
        "інцидентному ребру з motor_vehicle-доступом — і додає пішохідний "
        "сегмент <b>walk_time = gap_m / walk_speed</b>, якщо gap_m ≤ 100 м. "
        "Інакше точка позначається як недоступна для авто."
    ),
    H2("3.3. Аудит досяжності"),
    P(
        "<b>audit_reachability(matrix, nodes, labels)</b> сканує матрицю "
        "на PENALTY-клітинки і повертає список об'єктів "
        "<b>UnreachableStop</b> з полями: вузол, мітка ('Stop #2'), список "
        "пунктів, з яких/до яких неможливо дістатися. UI рендерить ці "
        "об'єкти як іменовані попередження."
    ),
    H2("3.4. Розв'язання TSP — автовибір методу"),
    P(
        "Функція <b>solve_tsp(nodes, matrix, method=\"auto\")</b> обирає "
        "стратегію залежно від розміру задачі:"
    ),
    Spacer(1, 2),
    table(
        [
            ["Кількість точок", "Метод", "Складність"],
            ["1–2", "Nearest-Neighbour (NN)", "O(n²)"],
            ["3–20", "NN + 2-opt", "O(n³) на ітерацію"],
            ["21+", "Genetic Algorithm (OX)", "O(P · G · n)"],
        ],
        col_widths=[3.6 * cm, 6.0 * cm, 4.0 * cm],
    ),
    Spacer(1, 6),
    note(
        "Чому 2-opt замість Christofides на малих інстансах: Christofides "
        "потребує повного зв'язного неорієнтованого графа. Якщо хоч одна "
        "пара має PENALTY-вагу, допоміжний граф розсипається на компоненти "
        "і алгоритм падає. 2-opt же коректно опрацьовує PENALTY — він "
        "просто ніколи не вибере таке ребро, якщо є скінченна альтернатива."
    ),
]

story += [PageBreak()]

# ── 3.5 GA TSP ─────────────────────────────────────────────────────────────
story += [
    H2("3.5. Покрокова робота 2-opt"),
    P(
        "2-opt починає з NN-маршруту й ітеративно інвертує пари ребер: "
        "для кожної пари індексів (i, j), 1 ≤ i < j < n, формує кандидата"
    ),
    code("candidate = route[:i] + route[i:j+1][::-1] + route[j+1:]"),
    P(
        "Якщо <b>cost(candidate) &lt; cost(best)</b>, заміняє best і "
        "продовжує. Алгоритм зупиняється, коли жодна інверсія не покращує "
        "вартість (локальний оптимум) або досягнуто 2000 ітерацій. "
        "Депо зафіксоване на позиції 0 і n-1, тому ніколи не свопається."
    ),
    H2("3.6. Покрокова робота генетичного алгоритму"),
    P(
        "Хромосома — перестановка індексів точок (без депо). Депо додається "
        "на декодуванні: route = [depot] + chrom + [depot]."
    ),
    *bullets([
        "<b>Ініціалізація:</b> populations_size=120 випадкових перестановок, "
        "плюс один NN-сід.",
        "<b>Селекція:</b> топ-10% популяції стають елітою.",
        "<b>Order Crossover (OX):</b> копіюється випадковий зріз p1[a:b] у "
        "дитину; решта позицій заповнюється генами p2 у тому порядку, у "
        "якому вони з'являються у p2 (без дублювань).",
        "<b>Swap-мутація:</b> з імовірністю mutation_rate=0.02 міняються "
        "значення у двох випадкових позиціях.",
        "<b>Fitness:</b> 1 / (cost + 1) — ε уникає ділення на нуль для "
        "PENALTY-маршрутів.",
        "<b>Покоління:</b> 400 (за замовчуванням), потім обирається "
        "найкраща хромосома.",
    ]),
    H2("3.7. Реконструкція повного маршруту"),
    P(
        "TSP повертає лише послідовність точок зупинок. Щоб намалювати "
        "полілінію на карті, потрібен повний шлях по OSM-вузлах. "
        "<b>reconstruct_full_route(G, tsp_route)</b> для кожної пари "
        "(stop[i], stop[i+1]) запускає Дейкстру і склеює сегменти, "
        "відкидаючи дублікати на стиках."
    ),
]

story += [PageBreak()]

# ── Step 5b: VRP ───────────────────────────────────────────────────────────
story += [
    H1("4. Оптимізація VRP (флот, CVRPTW)"),
    P(
        "Capacitated Vehicle Routing Problem with Time Windows: розподілити "
        "n точок між K транспортними засобами так, щоб мінімізувати"
    ),
    code("F = A · T_total + B · K + P · |U|"),
    P(
        "де <b>T_total</b> — сума часів усіх маршрутів, <b>K</b> — кількість "
        "активних ТЗ, <b>U</b> — множина незамаршрутизованих точок, "
        "<b>P ≫ A, B</b>. Обмеження: вантажопідйомність "
        "Q_θ та часові вікна [tw_open, tw_close] кожної точки."
    ),
    H2("4.1. Фаза 1 — призначення режимів"),
    P(
        "<b>_assign_stops_to_modes(stops, fleet, depot, graphs)</b>:"
    ),
    *bullets([
        "Для кожної точки <b>_check_reachable()</b> запускає Дейкстру у "
        "кожному з трьох графів і повертає множину досяжних режимів.",
        "Серед сумісних режимів обирається той, де залишок сумарної "
        "місткості флоту максимальний.",
        "Залишок місткості зменшується на weight_kg точки.",
        "Точки, недосяжні жодним режимом або з вичерпаним флотом, "
        "потрапляють у список unreachable.",
    ]),
    H2("4.2. Фаза 2 — GA для кожної групи режиму"),
    P(
        "Для кожного пулу точок одного режиму викликається "
        "<b>_genetic_algorithm()</b> з параметрами тези:"
    ),
    Spacer(1, 2),
    table(
        [
            ["Параметр", "Значення", "Призначення"],
            ["POP_SIZE", "50", "Розмір популяції"],
            ["N_GENERATIONS", "150", "Кількість поколінь"],
            ["CROSSOVER_RATE", "0.85", "Імовірність кросоверу"],
            ["MUTATION_RATE", "0.15", "Імовірність мутації"],
            ["ELITE_SIZE", "2", "Скільки найкращих переходить без змін"],
            ["TOURNAMENT_SIZE", "3", "Розмір турніру для селекції"],
            ["GA_SEED", "42", "Детермінованість запуску"],
        ],
        col_widths=[3.6 * cm, 2.4 * cm, 7.6 * cm],
    ),
    H2("4.3. Декодер Split"),
    P(
        "Хромосома — це 'гігантський тур' (перестановка всіх клієнтів пулу, "
        "без розділювачів між машинами). Декодер послідовно проходить "
        "хромосому й додає клієнта до поточного маршруту, поки виконуються:"
    ),
    *bullets([
        "<b>Capacity:</b> сума weight_kg ≤ vehicle.capacity_kg.",
        "<b>Time-window:</b> час прибуття ≤ tw_close клієнта (з "
        "урахуванням service_time попередніх).",
        "<b>Reachability:</b> жодне ребро маршруту не має PENALTY.",
        "<b>Round-trip:</b> після останнього клієнта ТЗ повертається у депо "
        "до tw_close депо.",
    ]),
    P(
        "Якщо клієнта не вдається додати, поточний маршрут закривається й "
        "береться наступний ТЗ із флоту. Невпихнуті клієнти потрапляють "
        "у <b>unassigned</b> і штрафуються через <b>UNROUTED_PENALTY = 1e6</b>."
    ),
    H2("4.4. Покоління GA"),
    *bullets([
        "<b>Турнірна селекція:</b> 3 випадкові — обирається найкращий.",
        "<b>OX-кросовер:</b> ймовірність 0.85.",
        "<b>Swap-мутація:</b> ймовірність 0.15.",
        "<b>Елітизм:</b> 2 найкращі без змін у наступне покоління.",
        "Найкраща хромосома за всі 150 поколінь декодується у фінальний "
        "розподіл маршрутів.",
    ]),
]

story += [PageBreak()]

# ── Output / visualization ─────────────────────────────────────────────────
story += [
    H1("5. Формування результату"),
    H2("5.1. Об'єкти-носії результату"),
    *bullets([
        "<b>ModeResult</b> (TSP) — режим, послідовність зупинок, повний "
        "маршрут, total_time_s, легі (LegInfo).",
        "<b>VehicleRoute</b> (VRP) — ТЗ, зупинки, tsp/full route, "
        "total_time_s, total_dist_m, легі, skipped_stops.",
        "<b>LegInfo</b> — пара (from, to), distance_m, travel_time_s, "
        "cumulative_time_s. Використовується для побудови таблиці "
        "черговості та ETA.",
    ]),
    H2("5.2. Візуалізація"),
    P(
        "<b>build_result_map()</b> — TSP. AntPath кожного режиму "
        "(drive — червоний, bike — зелений, walk — синій), LayerControl "
        "для перемикання, депо — зелений маркер, точки — нумеровані "
        "червоні."
    ),
    P(
        "<b>build_vrp_result_map()</b> — VRP. Кольорова полілінія для "
        "кожного ТЗ (палітра ColorBrewer Set1, 10 кольорів), маркери з "
        "номером ТЗ."
    ),
    H1("6. Що бачить користувач"),
    *bullets([
        "Інтерактивна анімована карта з маршрутами.",
        "Картки метрик: загальний час drive / bike / walk.",
        "Таблиця черговості: від → до, відстань, час етапу, ETA.",
        "Банери попереджень: колізії вузлів, недосяжні точки, "
        "недоступні для авто, простоюючі ТЗ.",
    ]),
    Spacer(1, 12),
    note(
        "Усі довгі обчислення кешуються у st.session_state з ключем "
        "(depot.lat, depot.lon, radius), тому повторне натискання "
        "'Optimize Route' не запускає завантаження мережі OSM повторно."
    ),
]

doc.build(story)
print(f"Generated: {OUT}")
