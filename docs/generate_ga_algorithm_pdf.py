"""Generate a step-by-step GA explanation PDF for DeliveryIQ.

Focuses specifically on the genetic / evolutionary algorithm in vrp_solver.py
that solves the CVRPTW (Capacitated VRP with Time Windows).
"""
from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_JUSTIFY, TA_LEFT, TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import registerFontFamily
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
pdfmetrics.registerFont(TTFont("Mono-Bold", str(WIN_FONTS / "consolab.ttf")))
registerFontFamily(
    "Body", normal="Body", bold="Body-Bold", italic="Body-Italic",
)
registerFontFamily(
    "Mono", normal="Mono", bold="Mono-Bold",
)

# ── Palette ─────────────────────────────────────────────────────────────────
ACCENT = HexColor("#1f4e79")
ACCENT2 = HexColor("#2c5282")
SOFT = HexColor("#f4f7fb")
BORDER = HexColor("#cbd5e1")
NOTE_BG = HexColor("#fffbeb")
NOTE_BORDER = HexColor("#facc15")
STEP_BG = HexColor("#eef5ff")
STEP_BORDER = HexColor("#9bbbe0")

# ── Styles ──────────────────────────────────────────────────────────────────
styles = getSampleStyleSheet()
title_style = ParagraphStyle(
    "Title", parent=styles["Title"], fontName="Body-Bold",
    fontSize=22, leading=28, textColor=ACCENT, alignment=TA_LEFT,
    spaceAfter=10,
)
subtitle_style = ParagraphStyle(
    "Sub", parent=styles["BodyText"], fontName="Body-Italic",
    fontSize=13, leading=16, textColor=HexColor("#475569"),
    spaceAfter=18,
)
h1_style = ParagraphStyle(
    "H1", parent=styles["Heading1"], fontName="Body-Bold",
    fontSize=16, leading=20, textColor=ACCENT,
    spaceBefore=14, spaceAfter=8,
)
h2_style = ParagraphStyle(
    "H2", parent=styles["Heading2"], fontName="Body-Bold",
    fontSize=13, leading=17, textColor=ACCENT2,
    spaceBefore=10, spaceAfter=4,
)
h3_style = ParagraphStyle(
    "H3", parent=styles["Heading3"], fontName="Body-Bold",
    fontSize=11.5, leading=15, textColor=HexColor("#334155"),
    spaceBefore=6, spaceAfter=3,
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
formula_style = ParagraphStyle(
    "Formula", parent=body_style, fontName="Body-Italic", fontSize=12,
    leading=16, alignment=TA_CENTER,
    textColor=HexColor("#1a365d"), backColor=SOFT, borderColor=BORDER,
    borderWidth=0.5, borderPadding=8, spaceBefore=4, spaceAfter=8,
)
note_style = ParagraphStyle(
    "Note", parent=body_style, fontName="Body-Italic", fontSize=10.5,
    textColor=HexColor("#475569"), leftIndent=10,
    backColor=NOTE_BG, borderColor=NOTE_BORDER,
    borderWidth=0.5, borderPadding=6, spaceBefore=4, spaceAfter=8,
)
step_style = ParagraphStyle(
    "Step", parent=body_style, fontName="Body",
    backColor=STEP_BG, borderColor=STEP_BORDER, borderWidth=0.5,
    borderPadding=8, leftIndent=0, rightIndent=0,
    spaceBefore=4, spaceAfter=8,
)
caption_style = ParagraphStyle(
    "Caption", parent=body_style, fontName="Body-Italic", fontSize=10,
    textColor=HexColor("#64748b"), alignment=TA_CENTER, spaceAfter=8,
)


# ── Helpers ─────────────────────────────────────────────────────────────────
def P(text, style=body_style):
    return Paragraph(text, style)


def H1(text):
    return P(text, h1_style)


def H2(text):
    return P(text, h2_style)


def H3(text):
    return P(text, h3_style)


def bullets(items):
    return [Paragraph(f"• {it}", bullet_style) for it in items]


def numbered(items):
    return [
        Paragraph(f"<b>{i + 1}.</b>&nbsp;&nbsp;{it}", bullet_style)
        for i, it in enumerate(items)
    ]


def code(text):
    safe = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    safe = safe.replace("\n", "<br/>")
    return Paragraph(safe, mono_style)


def formula(text):
    return Paragraph(text, formula_style)


def note(text):
    return Paragraph(text, note_style)


def step_box(title, body_html):
    text = f"<b>{title}</b><br/>{body_html}"
    return Paragraph(text, step_style)


def caption(text):
    return Paragraph(text, caption_style)


def table(rows, col_widths, header=True):
    t = Table(rows, colWidths=col_widths, hAlign="LEFT")
    style = [
        ("FONTNAME", (0, 0), (-1, -1), "Body"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("BACKGROUND", (0, 1), (-1, -1), SOFT),
        ("GRID", (0, 0), (-1, -1), 0.4, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    if header:
        style += [
            ("FONTNAME", (0, 0), (-1, 0), "Body-Bold"),
            ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
            ("TEXTCOLOR", (0, 0), (-1, 0), HexColor("#ffffff")),
        ]
    t.setStyle(TableStyle(style))
    return t


# ── Document ────────────────────────────────────────────────────────────────
OUT = Path(__file__).parent / "pdf" / "DeliveryIQ_GeneticAlgorithm.pdf"
OUT.parent.mkdir(parents=True, exist_ok=True)

doc = SimpleDocTemplate(
    str(OUT), pagesize=A4,
    leftMargin=2.0 * cm, rightMargin=2.0 * cm,
    topMargin=1.8 * cm, bottomMargin=1.8 * cm,
    title="Генетичний алгоритм еволюційного моделювання — DeliveryIQ",
    author="Tymur Nikolaiev",
)

story = []

# ── Title page ─────────────────────────────────────────────────────────────
story += [
    P("Генетичний алгоритм еволюційного моделювання", title_style),
    P("Покрокове пояснення алгоритму, реалізованого у застосунку DeliveryIQ "
      "(модуль <b>vrp_solver.py</b>)", subtitle_style),
    P(
        "Документ описує, як реалізовано генетичний алгоритм (ГА) "
        "еволюційного моделювання для розв'язання задачі маршрутизації "
        "транспортних засобів з обмеженнями вантажопідйомності та часовими "
        "вікнами (CVRPTW). Кожен крок алгоритму викладено послідовно: від "
        "кодування хромосоми до завершення еволюційного циклу та "
        "формування фінальних маршрутів."
    ),
    P(
        "Реалізація ґрунтується на класичній схемі ГА Голланда (1975) із "
        "адаптаціями для VRP: декодером Split (Prins, 2004), оператором "
        "впорядкованого схрещування Order Crossover (Oliver, Smith, Holland, "
        "1987) та елітизмом для збереження найкращих розв'язків."
    ),
]

# ── 1. Problem ──────────────────────────────────────────────────────────────
story += [
    H1("1. Постановка задачі"),
    P(
        "Задано <b>n</b> точок доставки, депо <b>D</b> та парк "
        "<b>K</b> транспортних засобів (ТЗ) з різними режимами руху "
        "(drive / bike / walk) та вантажопідйомностями <b>Q<sub>θ</sub></b>. "
        "Кожна точка має вагу <b>w<sub>i</sub></b> (кг), часове вікно "
        "<b>[tw_open<sub>i</sub>, tw_close<sub>i</sub>]</b> та час "
        "обслуговування <b>s<sub>i</sub></b>."
    ),
    P("Потрібно мінімізувати цільову функцію:"),
    formula("F = A · T<sub>total</sub> + B · K<sub>active</sub> + P · |U|"),
    P("де:"),
    *bullets([
        "<b>T<sub>total</sub></b> — сумарний час подорожі всіх ТЗ;",
        "<b>K<sub>active</sub></b> — кількість використаних ТЗ;",
        "<b>U</b> — множина незамаршрутизованих клієнтів;",
        "<b>A = 1.0, B = 0.0, P = 10<sup>6</sup></b> — вагові коефіцієнти "
        "(штраф P ≫ A,B змушує ГА уникати неповних розв'язків).",
    ]),
    P("Обмеження:"),
    *bullets([
        "<b>Capacity:</b> Σ w<sub>i</sub> по маршруту ≤ Q<sub>θ</sub>;",
        "<b>Time-window:</b> прибуття у точку i ≤ tw_close<sub>i</sub>;",
        "<b>Round-trip:</b> кожен ТЗ стартує з депо й повертається у депо "
        "до tw_close<sub>D</sub>;",
        "<b>Reachability:</b> кожне ребро маршруту прохідне для режиму ТЗ "
        "(вартість &lt; PENALTY = 10<sup>9</sup>).",
    ]),
]

# ── 2. Architecture ─────────────────────────────────────────────────────────
story += [
    H1("2. Двофазна архітектура"),
    P(
        "Алгоритм у <b>vrp_solver.py</b> побудовано як двофазну схему. "
        "Це принципове проєктне рішення: воно розбиває складну "
        "багатомодальну задачу CVRPTW на серію незалежних одно-модальних "
        "підзадач, кожну з яких ГА розв'язує ефективніше."
    ),
    Spacer(1, 4),
    table(
        [
            ["Фаза", "Функція", "Призначення"],
            ["1", "_assign_stops_to_modes()",
             "Розподіл точок між пулами режимів (drive/bike/walk) "
             "за критерієм досяжності та залишку місткості."],
            ["2", "_genetic_algorithm()",
             "Окремий запуск ГА у кожному пулі — пошук розподілу "
             "точок між ТЗ і порядку відвідання."],
        ],
        col_widths=[1.2 * cm, 5.0 * cm, 9.4 * cm],
    ),
    Spacer(1, 6),
    note(
        "Чому двофазна схема: ТЗ режиму <i>walk</i> не може використовувати "
        "ребра, прохідні лише для <i>bike</i>, тож зведення задачі до "
        "одного ГА над усіма точками потребувало б складніших обмежень "
        "у декодері. Розподіл за режимами заздалегідь спрощує операторів "
        "ГА і робить декодер чистішим."
    ),
]

story += [PageBreak()]

# ── 3. Encoding ─────────────────────────────────────────────────────────────
story += [
    H1("3. Кодування розв'язку — хромосома"),
    P(
        "Кожен <b>індивід</b> популяції кодується як <b>перестановка</b> "
        "індексів клієнтів пулу — так званий <i>гігантський тур</i> "
        "(giant tour). Розділювачів між маршрутами різних ТЗ у хромосомі "
        "<u>немає</u> — їх вставляє декодер під час оцінки."
    ),
    H3("Приклад"),
    P("Нехай у пулі режиму <i>drive</i> 6 точок з індексами 0…5. "
      "Хромосома може виглядати так:"),
    code("chromo = [3, 0, 5, 1, 4, 2]"),
    P(
        "Це означає: декодер спочатку спробує покласти у поточний "
        "маршрут точку 3, потім 0, потім 5 і т.д. Якщо у певний момент "
        "обмеження порушується — поточний маршрут закривається, "
        "береться наступний ТЗ із флоту."
    ),
    H3("Властивості кодування"),
    *bullets([
        "<b>Універсальна допустимість:</b> будь-яка перестановка валідна "
        "як хромосома — допустимість маршрутів забезпечує декодер.",
        "<b>Без надмірності:</b> один розв'язок ↔ одна хромосома "
        "(ігноруючи циклічні зсуви всередині сегментів).",
        "<b>Сумісність з операторами TSP:</b> класичні OX-кросовер та "
        "swap-мутація працюють напряму без модифікацій.",
    ]),
]

# ── 4. Initialization ───────────────────────────────────────────────────────
story += [
    H1("4. Ініціалізація популяції"),
    P(
        "Розмір популяції — <b>POP_SIZE = 50</b> хромосом. Популяція "
        "формується гібридно: один <b>теплий старт</b> + випадкове "
        "наповнення."
    ),
    H3("4.1. Жадібний сід (Nearest-Neighbour)"),
    P(
        "Функція <b>_nearest_neighbour_seed()</b> будує одну хромосому, "
        "обираючи на кожному кроці найближчу за часом подорожі ще не "
        "відвідану точку:"
    ),
    code(
        "current = depot_node\n"
        "while unvisited:\n"
        "    next = argmin_{c ∈ unvisited} matrix[current, stop_node[c]]\n"
        "    chromo.append(next)\n"
        "    current = stop_node[next]"
    ),
    H3("4.2. Випадкові перестановки"),
    P(
        "Решта (POP_SIZE − 1) хромосом — випадкові перестановки "
        "<b>list(range(n))</b>, перемішані через <b>rng.shuffle()</b>. "
        "Це забезпечує початкову різноманітність."
    ),
    note(
        "Чому саме гібридний старт: чисто випадкова популяція потребує "
        "багато поколінь, щоб дістатися до якісних розв'язків. Один NN-сід "
        "одразу задає верхню межу фітнесу — ГА може лише поліпшувати її "
        "далі. Чисто жадібна ініціалізація, навпаки, обмежить дослідження "
        "простору розв'язків і ризикує застрягти у локальному оптимумі."
    ),
]

story += [PageBreak()]

# ── 5. Fitness ──────────────────────────────────────────────────────────────
story += [
    H1("5. Функція пристосованості (фітнес)"),
    P(
        "Кожна хромосома оцінюється функцією <b>_evaluate()</b>, яка "
        "повертає трійку <b>(fitness, routes, unassigned)</b>. Чим менший "
        "фітнес — тим кращий індивід (мінімізаційна постановка)."
    ),
    H3("5.1. Декодер Split"),
    P(
        "Перш ніж порахувати фітнес, потрібно перетворити хромосому "
        "(перестановку) на конкретний розклад маршрутів — це робить "
        "<b>_decode_chromosome()</b> (метод Split, Prins 2004). Алгоритм "
        "проходить хромосому зліва направо й жадібно наповнює поточний ТЗ "
        "клієнтами:"
    ),
    *numbered([
        "Беремо першого ТЗ із флоту: <b>vehicle = vehicles[0]</b>, "
        "ініціалізуємо порожній <b>current_route = []</b>.",
        "Для кожного гену <b>cust</b> з хромосоми перевіряємо: "
        "чи буде маршрут <b>current_route + [cust]</b> допустимим для "
        "поточного ТЗ?",
        "<b>Якщо так</b> — додаємо клієнта до поточного маршруту й "
        "переходимо до наступного гену.",
        "<b>Якщо ні</b> — закриваємо поточний маршрут (вкладаємо у список "
        "<i>routes</i>), беремо наступний ТЗ і повторюємо перевірку для "
        "цього самого гену.",
        "Якщо клієнт не вмістився у жодного з решти ТЗ — він додається до "
        "<b>unassigned</b>.",
    ]),
    H3("5.2. Функція допустимості маршруту"),
    P(
        "Функція <b>route_feasible(route, vehicle)</b> перевіряє три "
        "обмеження одночасно (псевдокод):"
    ),
    code(
        "def route_feasible(route, vehicle):\n"
        "    # Capacity\n"
        "    if Σ weight[i] for i in route &gt; vehicle.capacity_kg:\n"
        "        return False\n"
        "\n"
        "    # Time-window + reachability simulation\n"
        "    prev = depot;  prev_begin = depot.tw_open\n"
        "    for cust in route:\n"
        "        tt = matrix[prev → cust]\n"
        "        if tt &gt;= PENALTY: return False    # impassable\n"
        "        arrival = prev_begin + service[prev] + tt\n"
        "        if arrival &gt; tw_close[cust]: return False  # late\n"
        "        prev_begin = max(arrival, tw_open[cust])  # wait if early\n"
        "        prev = cust\n"
        "\n"
        "    # Round-trip back to depot before its tw_close\n"
        "    tt_back = matrix[prev → depot]\n"
        "    if tt_back &gt;= PENALTY: return False\n"
        "    if prev_begin + service[prev] + tt_back &gt; depot.tw_close:\n"
        "        return False\n"
        "    return True"
    ),
    H3("5.3. Обчислення фітнесу"),
    P("Після декодування фітнес обчислюється за формулою:"),
    formula(
        "F = A · Σ T<sub>r</sub> + B · K + P · |U|"
    ),
    P(
        "де <b>T<sub>r</sub></b> — час маршруту r (час depot → c<sub>1</sub> "
        "→ … → c<sub>k</sub> → depot з матриці), <b>K</b> — кількість "
        "активних маршрутів, <b>|U|</b> — кількість невпихнутих клієнтів."
    ),
    note(
        "Штраф P · |U| змушує ГА спершу маршрутизувати ВСІХ клієнтів. "
        "Лише після цього оптимізатор починає скорочувати T<sub>total</sub>. "
        "Це властивість лексикографічної оптимізації."
    ),
]

story += [PageBreak()]

# ── 6. Operators ────────────────────────────────────────────────────────────
story += [
    H1("6. Генетичні оператори"),
    H2("6.1. Селекція — турнірний відбір"),
    P(
        "Турнір розміру <b>TOURNAMENT_SIZE = 3</b> працює так:"
    ),
    *numbered([
        "Випадково обираються 3 індекси з популяції (без повторень).",
        "Серед них обирається індивід з <b>найменшим</b> фітнесом.",
        "Цей індивід стає батьком для подальшого схрещування.",
    ]),
    P("Реалізація — <b>_tournament_select()</b>:"),
    code(
        "def tournament_select(pop, fitnesses, k=3):\n"
        "    candidates = rng.sample(range(len(pop)), k)\n"
        "    best = min(candidates, key=lambda i: fitnesses[i])\n"
        "    return pop[best]"
    ),
    note(
        "Чому турнір, а не пропорційна (рулеткова) селекція: турнір не "
        "залежить від абсолютних значень фітнесу й коректно працює, навіть "
        "коли частина хромосом має штраф P·|U| ≫ T<sub>total</sub>. "
        "Рулетка ж в такому випадку зосередила б усю ймовірність на одному-"
        "двох індивідах."
    ),
    H2("6.2. Кросовер — Order Crossover (OX)"),
    P(
        "OX — оператор для перестановок, що зберігає <i>відносний</i> "
        "порядок генів. На вхід — два батьки <b>p<sub>1</sub></b>, "
        "<b>p<sub>2</sub></b>; на вихід — одна дитина (<b>CROSSOVER_RATE = "
        "0.85</b> — імовірність застосування; інакше дитина = копія p₁)."
    ),
    H3("Покрокова робота OX"),
    *numbered([
        "Випадково обираються дві позиції <b>i &lt; j</b> у "
        "<b>[0, n−1]</b>.",
        "Сегмент <b>p<sub>1</sub>[i..j]</b> копіюється у дитину на ті ж "
        "позиції (це 'збережене ядро' від першого батька).",
        "Інші позиції дитини заповнюються генами з <b>p<sub>2</sub></b> "
        "у порядку появи, починаючи з позиції <b>j+1</b> (з циклічним "
        "обходом). Гени, вже присутні у ядрі, пропускаються.",
    ]),
    H3("Приклад OX (n = 6, i = 2, j = 4)"),
    code(
        "p1 = [3, 0, |5, 1, 4|, 2]      ← копіюємо сегмент [5,1,4]\n"
        "p2 = [2, 4, 1, 5, 3, 0]\n"
        "\n"
        "Кроки:\n"
        "  1) ядро дитини: [_, _, 5, 1, 4, _]\n"
        "  2) сканування p2 з позиції j+1=5:\n"
        "     [0, 2, 4 (skip), 1 (skip), 5 (skip), 3]\n"
        "     → корисні в порядку: 0, 2, 3\n"
        "  3) заповнення з позиції 5 (циклічно): pos 5 = 0,\n"
        "     pos 0 = 2, pos 1 = 3.\n"
        "\n"
        "child = [2, 3, 5, 1, 4, 0]"
    ),
    H2("6.3. Мутація — swap"),
    P(
        "З імовірністю <b>MUTATION_RATE = 0.15</b> у дитині міняються "
        "місцями значення двох випадкових позицій. Це найпростіша "
        "операція, що зберігає інваріант перестановки."
    ),
    code(
        "i, j = rng.sample(range(n), 2)\n"
        "chromo[i], chromo[j] = chromo[j], chromo[i]"
    ),
    note(
        "Чому swap, а не складніші мутації (inversion, scramble): декодер "
        "Split дуже чутливий до позиції генів — навіть мала зміна (1 swap) "
        "часто дає інший розподіл клієнтів між ТЗ. Сильніші мутації "
        "руйнують структуру маршрутів і фітнес різко погіршується."
    ),
    H2("6.4. Елітизм"),
    P(
        "Перед формуванням нового покоління <b>ELITE_SIZE = 2</b> "
        "найкращих хромосом з поточної популяції копіюються у нову без "
        "змін. Це гарантує, що найкращий знайдений фітнес <b>не зростає</b> "
        "від покоління до покоління."
    ),
    code(
        "order = sorted(range(POP_SIZE), key=lambda i: fitnesses[i])\n"
        "new_population = [population[i][:] for i in order[:ELITE_SIZE]]"
    ),
]

story += [PageBreak()]

# ── 7. Main loop ────────────────────────────────────────────────────────────
story += [
    H1("7. Основний еволюційний цикл"),
    P(
        "Функція <b>_genetic_algorithm()</b> об'єднує всі попередні "
        "компоненти у головний цикл:"
    ),
    code(
        "# 1) Initial population (1 NN seed + random fillers)\n"
        "population = [nearest_neighbour_seed(...)] + [shuffle(range(n)) ...]\n"
        "evaluations = [_evaluate(c, ...) for c in population]\n"
        "fitnesses = [e[0] for e in evaluations]\n"
        "best_eval = min(evaluations, key=lambda e: e[0])\n"
        "\n"
        "# 2) Evolutionary loop\n"
        "for generation in range(N_GENERATIONS):\n"
        "    # 2a) Elitism — keep the top E\n"
        "    order = sorted(range(POP_SIZE), key=lambda i: fitnesses[i])\n"
        "    new_pop = [population[i][:] for i in order[:ELITE_SIZE]]\n"
        "\n"
        "    # 2b) Fill the rest of the new population\n"
        "    while len(new_pop) &lt; POP_SIZE:\n"
        "        p1 = tournament_select(population, fitnesses)\n"
        "        p2 = tournament_select(population, fitnesses)\n"
        "        child = order_crossover(p1, p2) if rand &lt; CROSSOVER_RATE\n"
        "                else p1[:]\n"
        "        if rand &lt; MUTATION_RATE:\n"
        "            child = swap_mutation(child)\n"
        "        new_pop.append(child)\n"
        "\n"
        "    # 2c) Replace and re-evaluate\n"
        "    population = new_pop\n"
        "    evaluations = [_evaluate(c, ...) for c in population]\n"
        "    fitnesses = [e[0] for e in evaluations]\n"
        "\n"
        "    # 2d) Track best-so-far\n"
        "    gen_best = min(evaluations, key=lambda e: e[0])\n"
        "    if gen_best[0] &lt; best_eval[0]:\n"
        "        best_eval = gen_best\n"
        "\n"
        "# 3) Decode the best chromosome to final routes\n"
        "_, best_routes, unassigned = best_eval\n"
        "return best_routes"
    ),
    H2("7.1. Покрокове резюме одного покоління"),
    *numbered([
        "<b>Сортування:</b> популяція сортується за фітнесом за "
        "зростанням.",
        "<b>Елітизм:</b> топ-2 індивіди копіюються у нову популяцію.",
        "<b>Селекція:</b> турнір розміру 3 обирає двох батьків.",
        "<b>Кросовер:</b> з імовірністю 0.85 — OX; інакше дитина = копія "
        "першого батька.",
        "<b>Мутація:</b> з імовірністю 0.15 — swap двох позицій.",
        "<b>Додавання дитини</b> до нової популяції; повтор кроків 3–6, "
        "поки нова популяція не досягне POP_SIZE = 50.",
        "<b>Заміна:</b> стара популяція повністю замінюється на нову "
        "(generational replacement).",
        "<b>Переоцінка:</b> для всіх 50 нових хромосом викликається "
        "_evaluate() — обчислюється фітнес і декодуються маршрути.",
        "<b>Оновлення best-so-far:</b> якщо знайдено новий найкращий "
        "індивід — він зберігається.",
    ]),
]

story += [PageBreak()]

# ── 8. Termination ──────────────────────────────────────────────────────────
story += [
    H1("8. Завершення та формування результату"),
    H2("8.1. Критерій зупинки"),
    P(
        "У реалізації використано <b>фіксовану кількість поколінь</b> "
        "<b>N_GENERATIONS = 150</b>. Альтернативні критерії (стагнація "
        "найкращого фітнесу, ліміт часу) не застосовано — фіксована "
        "кількість поколінь забезпечує детерміновану тривалість запуску, "
        "що зручно для UI Streamlit."
    ),
    H2("8.2. Декодування найкращої хромосоми"),
    P(
        "Після завершення циклу зберігається <b>best_eval</b> — трійка "
        "(фітнес, маршрути, unassigned) для найкращого індивіда за всю "
        "історію. <b>best_routes</b> — це список пар "
        "<b>(Vehicle, [індекси клієнтів у порядку відвідання])</b>."
    ),
    H2("8.3. Реконструкція повного маршруту"),
    P(
        "Функція <b>_build_ga_routes()</b> для кожного маршруту:"
    ),
    *numbered([
        "Будує замкнений тур по OSM-вузлах: "
        "<b>tsp_route = [depot] + [stop_node[i] for i in indices] + [depot]</b>.",
        "Викликає <b>reconstruct_full_route(G, tsp_route)</b> — для кожної "
        "пари сусідніх вузлів запускає Дейкстру і склеює сегменти, утворюючи "
        "повну послідовність OSM-вузлів для рендеру полілінії.",
        "Підсумовує час за матрицею (для метрик) і довжину за атрибутом "
        "<b>length</b> ребер графа (для відображення в UI).",
        "Повертає об'єкт <b>VehicleRoute</b> з полями: ТЗ, упорядкований "
        "список зупинок, tsp_route, full_route, total_time_s, total_dist_m.",
    ]),
    H2("8.4. Обробка нерозподілених клієнтів"),
    P(
        "Якщо <b>unassigned</b> непорожній — це означає, що навіть найкращий "
        "індивід не зміг розмістити всі точки (через перевантаження флоту "
        "або жорсткі часові вікна). Logger пише попередження, а у UI "
        "з'являється банер з переліком пропущених адрес."
    ),
]

# ── 9. Hyperparameters ──────────────────────────────────────────────────────
story += [
    H1("9. Гіперпараметри"),
    P(
        "Усі параметри визначено у верхній частині "
        "<b>vrp_solver.py</b> (рядки ~80–92). Значення підібрані емпірично "
        "та відповідають рекомендаціям §2.7 дипломної роботи."
    ),
    Spacer(1, 4),
    table(
        [
            ["Параметр", "Значення", "Призначення"],
            ["POP_SIZE", "50",
             "Кількість хромосом у популяції."],
            ["N_GENERATIONS", "150",
             "Кількість ітерацій еволюційного циклу."],
            ["CROSSOVER_RATE", "0.85",
             "Імовірність застосування OX-кросоверу до пари батьків."],
            ["MUTATION_RATE", "0.15",
             "Імовірність swap-мутації для дитини."],
            ["ELITE_SIZE", "2",
             "Скільки найкращих індивідів переходить у наступне покоління "
             "без змін."],
            ["TOURNAMENT_SIZE", "3",
             "Розмір вибірки для турнірної селекції."],
            ["UNROUTED_PENALTY", "10⁶",
             "Штраф за кожного незамаршрутизованого клієнта (P у формулі "
             "фітнесу)."],
            ["GA_SEED", "42",
             "Зерно RNG; None — недетерміновані запуски."],
            ["OBJ_A", "1.0",
             "Вага сумарного часу T_total у фітнесі."],
            ["OBJ_B", "0.0",
             "Вага кількості активних ТЗ K (за замовчуванням вимкнена)."],
        ],
        col_widths=[4.0 * cm, 2.0 * cm, 9.6 * cm],
    ),
    Spacer(1, 8),
    note(
        "<b>GA_SEED = 42</b> робить запуски детермінованими: для тих самих "
        "вхідних даних ГА завжди повертає той самий розподіл маршрутів. Це "
        "критично для відтворюваності у дипломному дослідженні та для "
        "стабільності UI."
    ),
]

story += [PageBreak()]

# ── 10. Walkthrough ─────────────────────────────────────────────────────────
story += [
    H1("10. Покроковий приклад роботи"),
    P(
        "Розгляньмо умовний сценарій: 5 точок доставки, флот із двох ТЗ "
        "режиму <i>drive</i> з вантажопідйомностями 10 кг та 8 кг."
    ),
    H3("Вхідні дані"),
    table(
        [
            ["№", "Адреса", "Вага (кг)", "Часове вікно"],
            ["0", "вул. А, 12", "3.5", "09:00–12:00"],
            ["1", "вул. Б, 7", "5.0", "10:00–14:00"],
            ["2", "пр. В, 33", "2.0", "09:00–18:00"],
            ["3", "вул. Г, 4", "4.5", "11:00–15:00"],
            ["4", "вул. Д, 21", "3.0", "09:00–18:00"],
        ],
        col_widths=[1.0 * cm, 5.6 * cm, 2.4 * cm, 4.0 * cm],
    ),
    Spacer(1, 6),
    H3("Крок 1. Ініціалізація"),
    step_box(
        "Початкова популяція (50 хромосом):",
        "• NN-сід (нагадаємо, перший знайдений жадібно): "
        "<b>[2, 4, 0, 1, 3]</b><br/>"
        "• 49 випадкових перестановок: "
        "<b>[3, 1, 4, 0, 2]</b>, <b>[0, 2, 1, 3, 4]</b>, … "
    ),
    H3("Крок 2. Перша оцінка"),
    step_box(
        "Декодер Split проходить кожну хромосому. Наприклад, для NN-сіду:",
        "<font face='Mono' size='9.5'>chromo = [2, 4, 0, 1, 3]</font><br/>"
        "ТЗ #1 (Q=10): додає 2 (w=2.0, ост.=8.0), 4 (w=3.0, ост.=5.0), "
        "0 (w=3.5, ост.=1.5). Спроба додати 1 (w=5.0) — ні, перевищує "
        "місткість.<br/>"
        "→ маршрут №1: depot → 2 → 4 → 0 → depot.<br/>"
        "ТЗ #2 (Q=8): додає 1 (w=5.0), 3 (w=4.5) — сумарно 9.5, "
        "перевищення. Беремо лише 1, потім 3 не вміщається. Якщо ТЗ "
        "більше немає — 3 потрапляє у unassigned.<br/>"
        "<i>Фітнес = T_total + 10⁶ · 1 ≈ велике число</i>"
    ),
    H3("Крок 3. Селекція + кросовер"),
    step_box(
        "Турнір обирає двох батьків (припустимо):",
        "p1 = <b>[2, 4, 0, 1, 3]</b> (NN-сід, фітнес найнижчий)<br/>"
        "p2 = <b>[3, 1, 4, 0, 2]</b><br/>"
        "OX з i=1, j=3 → ядро p1[1..3] = [4, 0, 1]<br/>"
        "Заповнення з p2: на позиціях 4, 0 ставимо <b>3, 2</b><br/>"
        "child = <b>[2, 4, 0, 1, 3]</b>"
    ),
    H3("Крок 4. Мутація"),
    step_box(
        "З імовірністю 0.15 застосовується swap. Припустимо, обрано "
        "позиції 0 та 4:",
        "child = [2, 4, 0, 1, 3] → <b>[3, 4, 0, 1, 2]</b><br/>"
        "Тепер декодер може знайти інший розподіл — наприклад, ТЗ #1 "
        "везе [3, 4, 0] а ТЗ #2 везе [1, 2]. Якщо так — точка 3 "
        "розподілена і штраф P зникне."
    ),
    H3("Крок 5. Покоління 2…150"),
    step_box(
        "Цикл повторюється:",
        "З кожним поколінням еліта 2 індивідів зберігається без змін, "
        "решта 48 формуються через селекцію → кросовер → мутацію. "
        "Найкращий фітнес монотонно не зростає (елітизм гарантує). "
        "На пізніх поколіннях популяція збігається — більшість хромосом "
        "схожі, мутація приносить дрібні поліпшення."
    ),
    H3("Крок 6. Фінал"),
    step_box(
        "Після 150 поколінь:",
        "Беремо best_eval (зберігся з усієї історії) → декодуємо у "
        "<b>best_routes</b> = [(ТЗ #1, [2, 4, 0]), (ТЗ #2, [1, 3])].<br/>"
        "Кожен маршрут реконструюється у повну послідовність OSM-вузлів "
        "через Дейкстру. UI Streamlit рендерить дві кольорові полілінії "
        "на карті Folium."
    ),
]

story += [PageBreak()]

# ── 11. Theoretical justification ───────────────────────────────────────────
story += [
    H1("11. Обґрунтування проєктних рішень"),
    H2("11.1. Чому ГА, а не точний метод"),
    P(
        "CVRPTW — NP-важка задача. Точні методи (branch-and-cut, "
        "column generation) масштабуються лише до ~100 клієнтів і "
        "потребують комерційних розв'язувачів (CPLEX, Gurobi). "
        "ГА — метаевристика, що дає якісне (хоч і не гарантовано "
        "оптимальне) рішення за прийнятний час, працює без зовнішніх "
        "залежностей і легко розширюється."
    ),
    H2("11.2. Чому permutation encoding замість explicit assignment"),
    P(
        "Альтернатива — кодувати кожну хромосому як <b>[ТЗ, точка, "
        "позиція]</b>-трійки. Проте таке кодування створює <i>надмірність</i> "
        "(декілька кодів описують один розв'язок) і вимагає складних "
        "операторів кросоверу, що зберігають допустимість. Permutation "
        "encoding простіший і використовує перевірені оператори TSP."
    ),
    H2("11.3. Чому Split-декодер"),
    P(
        "Декодер Split (Prins, 2004) — стандарт для VRP-ГА. Його ключові "
        "переваги:"
    ),
    *bullets([
        "<b>Конструктивність:</b> завжди повертає допустимий розклад "
        "(або позначає клієнтів як unassigned).",
        "<b>Лінійна складність:</b> O(n · K) по одному проходу хромосоми.",
        "<b>Сумісність з обмеженнями:</b> capacity, time-windows і "
        "reachability перевіряються в одному циклі.",
    ]),
    H2("11.4. Чому elitism = 2"),
    P(
        "Елітизм запобігає втраті найкращого розв'язку через стохастику "
        "операторів. Розмір 2 (4% популяції) — компроміс: достатньо, щоб "
        "захистити best-so-far, але не настільки великий, щоб задушити "
        "різноманіття."
    ),
    H2("11.5. Чому штраф P · |U| замість жорсткого обмеження"),
    P(
        "У ранніх поколіннях деякі хромосоми неминуче будуть "
        "infeasible — флот, наприклад, тимчасово перевантажений. Жорстка "
        "відмова таких розв'язків (фітнес = +∞) знищила б усю популяцію. "
        "Штраф P ≫ A робить infeasible-розв'язки <i>дуже поганими</i>, "
        "але порівнянними між собою — ГА може поступово рухатися до "
        "feasible-області."
    ),
]

# ── 12. Algorithm flow diagram (ascii) ──────────────────────────────────────
story += [
    H1("12. Підсумкова схема алгоритму"),
    code(
        "  ┌────────────────────────────────────────────────────────────┐\n"
        "  │                  solve_vrp(stops, depot, fleet, graphs)    │\n"
        "  └─────────────────────────┬──────────────────────────────────┘\n"
        "                            ▼\n"
        "          ┌─────────────────────────────────────┐\n"
        "          │ Phase 1: _assign_stops_to_modes()   │\n"
        "          │  • Dijkstra reachability per mode   │\n"
        "          │  • Greedy capacity-balanced split   │\n"
        "          └────────┬─────────┬─────────┬────────┘\n"
        "                   ▼         ▼         ▼\n"
        "                drive_pool  bike_pool  walk_pool\n"
        "                   │         │         │\n"
        "                   ▼         ▼         ▼\n"
        "          ┌────────────────────────────────────┐\n"
        "          │  Phase 2: _genetic_algorithm() ×3  │\n"
        "          │  ┌──────────────────────────────┐  │\n"
        "          │  │ Init pop = NN seed + random  │  │\n"
        "          │  │              ▼               │  │\n"
        "          │  │  ┌────────────────────────┐  │  │\n"
        "          │  │  │ Evaluate (Split + F)   │◀─┐  │  │\n"
        "          │  │  └───────────┬────────────┘ │  │  │\n"
        "          │  │              ▼              │  │  │\n"
        "          │  │  Elitism (top-2)            │  │  │\n"
        "          │  │              ▼              │  │  │\n"
        "          │  │  Tournament select          │  │  │\n"
        "          │  │              ▼              │  │  │\n"
        "          │  │  Order Crossover (p=0.85)   │  │  │\n"
        "          │  │              ▼              │  │  │\n"
        "          │  │  Swap mutation (p=0.15)     │  │  │\n"
        "          │  │              ▼              │  │  │\n"
        "          │  │  New population             ──┘  │  │\n"
        "          │  │   (repeat × 150 generations)     │  │\n"
        "          │  └──────────────────────────────┘  │\n"
        "          └────────────────┬───────────────────┘\n"
        "                           ▼\n"
        "          ┌────────────────────────────────────┐\n"
        "          │  _build_ga_routes()                │\n"
        "          │  • Reconstruct OSM full path       │\n"
        "          │  • Compute time + distance         │\n"
        "          └────────────────┬───────────────────┘\n"
        "                           ▼\n"
        "          list[VehicleRoute]  →  Folium visualization"
    ),
    Spacer(1, 12),
    note(
        "Цей документ описує алгоритм у тому вигляді, у якому він "
        "реалізований у файлі <b>vrp_solver.py</b> на момент "
        "генерації PDF. Для перегляду актуального коду див. "
        "<font face='Mono'>_genetic_algorithm()</font>, "
        "<font face='Mono'>_decode_chromosome()</font> та "
        "<font face='Mono'>_evaluate()</font>."
    ),
]

doc.build(story)
print(f"Generated: {OUT}")
