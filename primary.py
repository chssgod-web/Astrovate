            ).classes("muted").style("margin:12px 0 24px;")
    with ui.column().style("width:min(1180px, 100%); margin:0 auto; padding:18px 16px 32px; gap:14px;"):
        boxes["header"] = ui.column().classes("w-full")
        with ui.row().classes("w-full").style("gap:14px; align-items:flex-start; flex-wrap:wrap;"):
            boxes["map"] = ui.column().style("flex:1 1 340px; min-width:280px;")
            boxes["panel"] = ui.column().style("flex:1.3 1 420px; min-width:300px;")
        with ui.row().classes("w-full").style("gap:14px; align-items:flex-start; flex-wrap:wrap;"):
            with ui.card().classes("panel").style("flex:1 1 340px; min-width:280px;"):
                ui.label("Cognitive reaction test").classes("section-title")
                ui.label(
                    "Five flashes. Tap the circle as soon as it turns cyan. "
                    "Timing is measured in this browser, then the average is written to today’s "
                    "reaction_time and the neurobehavioral score is recalculated."
                ).classes("muted")
                boxes["cog_prompt"] = ui.label("Ready when you are.")
                pad = ui.html(
                    """
                    <div id="astro-pad" style="width:180px;height:180px;border-radius:999px;
                    display:flex;align-items:center;justify-content:center;font-weight:800;
                    font-size:28px;background:#1A2436;color:#8AA0B8;cursor:pointer;
                    user-select:none;margin:12px auto;border:2px solid #2E3E56;
                    font-family:Segoe UI, Arial, sans-serif;">WAIT</div>
                    """,
                    sanitize=False,
                )
                pad.on("click", on_pad, js_handler=TAP_JS)
                boxes["cog_start"] = ui.button(
                    "Run Cognitive Reaction Test",
                    on_click=start_cognitive,
                ).props("no-caps")
                boxes["cog_result"] = ui.label("")
                boxes["reaction_caption"] = ui.label("").classes("muted")
            with ui.card().classes("panel").style("flex:1 1 340px; min-width:280px;"):
                ui.label("Daily crew log").classes("section-title")
                ui.label(
                    "These four entries write straight to SQLite and recolor the body map."
                ).classes("muted")
                today = latest_row()
                state["mood"] = int(today["mood_score"])
                state["eyes"] = int(today["ocular_headache_flag"])
                state["symptoms"] = int(today["radiation_symptoms"])
                state["pain"] = int(today["back_joint_pain"])
                boxes["mood_caption"] = ui.label(f"Mood — {MOOD_WORDS[state['mood']]}")
                ui.slider(min=1, max=5, step=1, value=state["mood"]).on_value_change(
                    lambda e: (
                        boxes["mood_caption"].set_text(f"Mood — {MOOD_WORDS[int(round(float(e.value)))]}"),
                        persist_symptom("mood_score", int(round(float(e.value))), "mood"),
                    )
                ).props("label-always")
                boxes["eye_caption"] = ui.label(f"Eyes — {EYE_WORDS[state['eyes']]}")
                ui.toggle(
                    {0: "0 Clear", 1: "1 Mild headache", 2: "2 Blurry / SANS"},
                    value=state["eyes"],
                ).on_value_change(
                    lambda e: (
                        boxes["eye_caption"].set_text(f"Eyes — {EYE_WORDS[int(e.value)]}"),
                        persist_symptom("ocular_headache_flag", int(e.value), "eyes"),
                    )
                )
                boxes["sym_caption"] = ui.label(f"Radiation symptoms — {SYM_WORDS[state['symptoms']]}")
                ui.slider(min=0, max=3, step=1, value=state["symptoms"]).on_value_change(
                    lambda e: (
                        boxes["sym_caption"].set_text(
                            f"Radiation symptoms — {SYM_WORDS[int(round(float(e.value)))]}"
                        ),
                        persist_symptom("radiation_symptoms", int(round(float(e.value))), "symptoms"),
                    )
                )
                boxes["pain_caption"] = ui.label(f"Back / joint pain — {PAIN_WORDS[state['pain']]}")
                ui.slider(min=1, max=5, step=1, value=state["pain"]).on_value_change(
                    lambda e: (
                        boxes["pain_caption"].set_text(
                            f"Back / joint pain — {PAIN_WORDS[int(round(float(e.value)))]}"
                        ),
                        persist_symptom("back_joint_pain", int(round(float(e.value))), "pain"),
                    )
                )
        boxes["charts"] = ui.column().classes("w-full")
    refresh()
@ui.page("/")
def index() -> None:
    build_page()
def pick_port(start: int = 8080) -> int:
    for port in range(start, start + 15):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    return start
def main() -> None:
    init_db()
    problems = self_check()
    if "--check" in sys.argv:
        if problems:
            print("SELF-CHECK FAILED")
            for problem in problems:
                print(" -", problem)
            sys.exit(1)
        print("SELF-CHECK OK")
        return
    if problems:
        print("Self-check warnings (the window will still open):")
        for problem in problems:
            print(" -", problem)
    else:
        print("Self-check passed: personal HR delta, SPE coupling, exercise streak.")
    port = int(os.environ.get("ASTROTWIN_PORT", "0") or 0) or pick_port()
    print(f"AstroTwin offline at http://127.0.0.1:{port}")
    ui.run(
        host="127.0.0.1",
        port=port,
        title="AstroTwin — Deep-Space Health & Radiation Monitor",
        reload=False,
        show=os.environ.get("ASTROTWIN_OPEN", "1") != "0",
    )
if __name__ == "__main__":
    main()