"""Self-contained examples of the public SloppyService v1 bubble contract."""

from copy import deepcopy


def response(kind="single_choice", choices=None, custom=False, **bounds):
    return dict(value_type=kind, allow_custom=custom, choices=choices or [], **bounds)


def choice(id, label, value):
    return dict(id=id, label=label, value=value)


APPROVAL = response(
    choices=[
        choice("approve", "Approve once", {"decision": "approve"}),
        choice("reject", "Reject", {"decision": "reject"}),
    ]
)


def question(id, title, spec, required=True):
    return dict(id=id, title=title, description="", required=required, response=spec)


def preset(id, name, body="message", kind="information", spec=None, **parameters):
    return {
        "id": id,
        "name": name,
        "notification": dict(
            kind=kind,
            title=name,
            message="",
            requires_response=spec is not None,
            **({"response": deepcopy(spec)} if spec else {}),
            bubble=dict(type=body, parameters=parameters),
        ),
    }


CATALOG = [
    preset("information", "Information", text="A quiet update from the testing plugin."),
    preset("success", "Success", kind="success", text="All focused checks passed."),
    preset("warning", "Warning", kind="warning", text="Something deserves a closer look."),
    preset("error", "Error", kind="error", text="A simulated failure; nothing was executed."),
    preset(
        "permission",
        "Permission request",
        kind="approval",
        spec=APPROVAL,
        text="Allow this simulated operation? No commands or file changes will run.",
    ),
    preset(
        "question",
        "Question with choices",
        kind="question",
        spec=response(
            choices=[choice("soft", "Keep it soft", "soft"), choice("bold", "Try bolder", "bold")],
            custom=True,
        ),
        text="Which direction feels right? You can also write your own answer.",
    ),
    preset(
        "text",
        "Text reply",
        kind="question",
        spec=response("text", custom=True),
        text="What should I focus on next?",
    ),
    preset(
        "boolean",
        "Boolean reply",
        kind="question",
        spec=response(
            "boolean", choices=[choice("yes", "Yes", True), choice("no", "No", False)], custom=True
        ),
        text="Enable the simulated feature?",
    ),
    preset(
        "number",
        "Bounded number reply",
        kind="question",
        spec=response("number", custom=True, minimum=1, maximum=5),
        text="Enter a retry count between 1 and 5.",
    ),
    preset(
        "json",
        "JSON reply",
        kind="question",
        spec=response("json", custom=True),
        text="Enter any JSON value. This tests the generic JSON reply control.",
    ),
    preset(
        "long-message",
        "Long message / resize test",
        text="\n".join(
            f"Line {i}: resize this bubble until all of its content fits." for i in range(1, 51)
        ),
    ),
    preset(
        "command",
        "Command approval",
        body="command",
        kind="approval",
        spec=APPROVAL,
        command="printf 'This is display-only; the testing plugin runs nothing.\\n'",
        working_directory="/example/project",
    ),
    *[
        preset(
            f"diff-{operation}",
            f"Diff: {operation}",
            body="diff",
            kind="approval",
            spec=APPROVAL,
            files=[
                dict(
                    path="example/config.txt",
                    operation=operation,
                    diff="--- a/example/config.txt\n+++ b/example/config.txt\n"
                    "@@ -1 +1 @@\n-old value\n+new value",
                )
            ],
        )
        for operation in ["create", "edit", "delete", "rename"]
    ],
    preset(
        "long-diff",
        "Long diff / scrolling and resize",
        body="diff",
        kind="approval",
        spec=APPROVAL,
        files=[
            dict(
                path="example/long-file.txt",
                operation="edit",
                diff="--- a/example/long-file.txt\n+++ b/example/long-file.txt\n@@ -1 +1,100 @@\n"
                + "\n".join(
                    f"+ Added line {i}: test wheel scrolling after resizing." for i in range(1, 101)
                ),
            )
        ],
    ),
    preset(
        "questionnaire",
        "Questionnaire: choices and text",
        body="questionnaire",
        kind="question",
        spec=response("json", custom=True),
        questions=[
            question(
                "database",
                "Which database?",
                response(
                    choices=[
                        choice("sqlite", "SQLite", "sqlite"),
                        choice("postgres", "PostgreSQL", "postgres"),
                    ],
                    custom=True,
                ),
            ),
            question("notes", "Anything else?", response("text", custom=True), required=False),
        ],
    ),
    preset(
        "multiple-choice",
        "Questionnaire: multiple choice",
        body="questionnaire",
        kind="question",
        spec=response("json", custom=True),
        questions=[
            question(
                "areas",
                "What should the review cover?",
                response(
                    "multi_choice",
                    choices=[
                        choice("api", "API contract", "api"),
                        choice("native", "Native presentation", "native"),
                        choice("recovery", "Outage recovery", "recovery"),
                    ],
                    custom=True,
                ),
            )
        ],
    ),
    preset(
        "typed-input",
        "Questionnaire: boolean, number, JSON",
        body="questionnaire",
        kind="question",
        spec=response("json", custom=True),
        questions=[
            question("enabled", "Enable notifications?", response("boolean", custom=True)),
            question(
                "retries",
                "Retry count (1-5)",
                response("number", custom=True, minimum=1, maximum=5),
            ),
            question("config", "Additional JSON configuration", response("json", custom=True)),
        ],
    ),
    preset(
        "long-questionnaire",
        "Long questionnaire / scrolling",
        body="questionnaire",
        kind="question",
        spec=response("json", custom=True),
        questions=[
            question(f"answer-{i}", f"Test question {i}", response("text", custom=True))
            for i in range(1, 13)
        ],
    ),
    preset(
        "resources",
        "Resource consent",
        body="resources",
        kind="approval",
        spec=APPROVAL,
        resources=[
            dict(kind=kind, name=name, access=access)
            for kind, name, access in [
                ("file", "/example/project/config.json", "Read the configuration"),
                ("directory", "/example/project/docs", "Read documentation"),
                ("network", "api.example.test:443", "HTTPS requests"),
                ("tool", "Issue tracker / list_issues", "Read open issues"),
                ("capability", "example-feature", "Use a simulated capability"),
            ]
        ],
    ),
    preset(
        "plan",
        "Plan review",
        body="plan",
        kind="approval",
        spec=response(
            choices=[
                choice("approve", "Approve plan", "approve"),
                choice("revise", "Revise", "revise"),
                choice("reject", "Decline", "decline"),
            ],
            custom=True,
        ),
        steps=["Read the request.", "Run focused checks.", "Report the result."],
        cautions=["This is a test. No steps will actually execute."],
    ),
    preset(
        "progress",
        "Progress: percentage",
        body="progress",
        kind="progress",
        label="Checking the changed files",
        fraction=0.65,
        steps=[
            dict(label=label, status=status)
            for label, status in [
                ("Read the contract", "done"),
                ("Run checks", "active"),
                ("Summarize", "pending"),
                ("Simulated failure", "failed"),
            ]
        ],
    ),
    preset(
        "indeterminate",
        "Progress: indeterminate",
        body="progress",
        kind="progress",
        label="Waiting for a simulated build",
        fraction=None,
        steps=[],
    ),
    preset(
        "report",
        "Result report",
        body="report",
        kind="success",
        sections=[
            dict(heading="Summary", body="All simulated checks passed.", code=False),
            dict(heading="Output", body="protocol: passed\ncontrols: passed", code=True),
        ],
    ),
    preset(
        "error-report",
        "Error report with retry",
        body="report",
        kind="error",
        spec=response(
            choices=[choice("retry", "Retry", "retry"), choice("dismiss", "Dismiss", "dismiss")]
        ),
        sections=[
            dict(heading="What happened", body="A simulated connection failure.", code=False)
        ],
    ),
    preset("avatar", "Avatar state and tint", text="Temporary happy state with mild red eyes."),
]
CATALOG[-1]["notification"]["avatar"] = dict(state="happy", red_eye_intensity=0.2)


def find_preset(id):
    for item in CATALOG:
        if item["id"] == id:
            return deepcopy(item["notification"])
    raise ValueError(f"Unknown preset: {id}")


def sample_value(spec):
    choices = spec.get("choices", [])
    kind = spec["value_type"]
    if choices:
        value = deepcopy(choices[0]["value"])
        return [value] if kind == "multi_choice" else value
    if kind == "boolean":
        return True
    if kind == "number":
        minimum, maximum = spec.get("minimum"), spec.get("maximum")
        value = 1 if minimum is None else minimum
        return min(value, maximum) if maximum is not None else value
    if kind == "multi_choice":
        return ["testing"]
    if kind == "json":
        return {"example": True}
    return "Testing response"


def default_answer(item):
    body = item.get("bubble") or {}
    if body.get("type") == "questionnaire":
        return {
            "selected_choice_ids": [],
            "value": {
                field["id"]: sample_value(field["response"])
                for field in body["parameters"]["questions"]
            },
        }
    spec = item["response"]
    choices = spec.get("choices", [])
    return {
        "selected_choice_ids": [choices[0]["id"]] if choices else [],
        "value": sample_value(spec),
    }
