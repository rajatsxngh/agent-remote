"""The Mac screen logic, checked against a small hand-made tree shaped like Claude's."""

from ps5_agent_handler.platform.mac_screen import ClaudeScreen, Node


def build():
    root = Node(role="AXApplication")
    window = root.add(Node(role="AXWindow", rect=(0, 0, 1400, 900)))
    web = window.add(Node(role="AXWebArea", rect=(0, 0, 1400, 900)))

    sidebar = web.add(Node(role="AXGroup", name="Sidebar", rect=(0, 0, 300, 900)))
    row1 = sidebar.add(Node(role="AXButton", name="Fix login Running", rect=(10, 100, 290, 130)))
    row1.add(Node(role="AXStaticText", value="Running", name="Running"))
    row2 = sidebar.add(Node(role="AXButton", name="Deny the refund", rect=(10, 140, 290, 170), current="page"))
    row2.add(Node(role="AXStaticText", name="Awaiting input", value="Awaiting input"))

    pane = web.add(Node(role="AXGroup", name="Primary pane", rect=(300, 0, 1400, 900)))
    chat = pane.add(Node(role="AXGroup", name="Chat messages", rect=(320, -5000, 1200, 800)))
    card = chat.add(Node(role="AXGroup", classes=("epitaxy-approval-card", "relative"), rect=(400, 300, 1100, 700)))
    block = card.add(Node(role="AXGroup", classes=("contents",), rect=(400, 320, 1100, 600)))
    block.add(Node(role="AXCheckBox", name="Explore project", classes=("epitaxy-answer-option",), value=1, rect=(410, 330, 1090, 380)))
    block.add(Node(role="AXCheckBox", name="Quick task", classes=("epitaxy-answer-option",), value=0, rect=(410, 390, 1090, 440), focused=True))
    other_row = block.add(Node(role="AXGroup", classes=("epitaxy-answer-option", "relative"), rect=(410, 450, 1090, 500)))
    other_row.add(Node(role="AXCheckBox", name="Other", value=0, rect=(410, 450, 1090, 500)))
    footer = card.add(Node(role="AXGroup", classes=("contents",), rect=(400, 620, 1100, 680)))
    footer.add(Node(role="AXButton", name="Skip", rect=(800, 630, 880, 670)))
    footer.add(Node(role="AXButton", name="Submit", rect=(900, 630, 1080, 670)))

    pane.add(Node(role="AXButton", name="Stop", shortcut="Escape", rect=(1200, 820, 1240, 860)))
    pane.add(Node(role="AXButton", name="Model: Opus 5.5 Medium", rect=(900, 860, 1100, 890)))
    pane.add(Node(role="AXTextArea", name="Prompt", classes=("tiptap", "ProseMirror"), value="", rect=(400, 800, 1150, 840)))
    return ClaudeScreen(root)


def test_buttons_come_from_the_main_pane_only():
    names = [b.name for b in build().buttons()]
    assert "Stop" in names and "Submit" in names
    assert "Deny the refund" not in names  # a sidebar session, never an answer to a prompt


def test_session_statuses_and_the_open_session():
    screen = build()
    states = {s.title: s.state for s in screen.session_statuses()}
    assert states == {"Fix login": "running", "Deny the refund": "attention"}
    assert screen.current_session_title() == "Deny the refund"


def test_prompt_box():
    prompt = build().prompt()
    assert prompt is not None and prompt.name == "Prompt"


def test_question_card_answers():
    screen = build()
    answers = screen.question_answers()
    assert [a["name"] for a in answers] == ["Explore project", "Quick task", "Other"]
    assert [a["checked"] for a in answers] == [True, False, False]
    assert [a["focused"] for a in answers] == [False, True, False]
    assert len({a["group"] for a in answers}) == 1  # all one question


def test_conversation_point_is_inside_the_visible_chat():
    x, y = build().conversation_point()
    assert 320 <= x <= 1200 and 0 <= y <= 800


def test_menu_items():
    screen = build()
    menu = screen.root.children[0].add(Node(role="AXMenu"))
    menu.add(Node(role="AXMenuItem", name="Opus 5.5", mark="✓"))
    menu.add(Node(role="AXMenuItem", name="Sonnet 5.5", mark=""))
    items = ClaudeScreen(screen.root).menu_items()
    assert [(i.name, i.checked) for i in items] == [("Opus 5.5", True), ("Sonnet 5.5", False)]
