"""Live progress (SSE), resume version history, and fit re-scored on the tailored resume."""
import json

from app.agents.fit_scorer import FitResult, compare_fit
from app.agents.progress import listening, say
from app.models import JobSession
from app.rag.store import TextIndex
from tests.conftest import FakeEmbedder, make_user
from tests.test_agents import FIT_FINAL, start_session, use_llm  # noqa: F401
from tests.test_tailor_cover import TAILORED, upload_resume

STREAM = {"Accept": "text/event-stream"}
# The original resume's fit: AWS not found. The tailored version shows it (FIT_FINAL has AWS strong).
FIT_BEFORE = json.dumps({**json.loads(FIT_FINAL), "requirements": [
    r if r["id"] != "M4" else {"id": "M4", "match": "missing", "evidence": None}
    for r in json.loads(FIT_FINAL)["requirements"]]})


def events(body: str) -> list[tuple[str, dict]]:
    out = []
    for block in body.split("\n\n"):
        lines = [ln for ln in block.splitlines() if not ln.startswith(":")]
        if not lines:
            continue
        event = next(ln[7:] for ln in lines if ln.startswith("event: "))
        data = json.loads("".join(ln[6:] for ln in lines if ln.startswith("data: ")))
        out.append((event, data))
    return out


# ---------- progress ----------

def test_say_is_a_no_op_without_a_listener_and_reports_with_one():
    say("nobody hears this")
    heard = []
    with listening(heard.append):
        say("step 1")
        say("step 2")
    say("after")
    assert heard == ["step 1", "step 2"]


def test_a_broken_listener_never_breaks_the_agent():
    def boom(_):
        raise RuntimeError("socket closed")

    with listening(boom):
        say("still fine")


# ---------- streaming endpoints ----------

def test_fit_streams_progress_then_the_saved_session(logged_in, use_llm, resume_index):  # noqa: F811
    upload_resume(logged_in)
    s = start_session(logged_in, use_llm)
    use_llm(FIT_FINAL)
    r = logged_in.post(f"/sessions/{s['id']}/fit", headers=STREAM)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    evs = events(r.text)
    progress = [d["message"] for e, d in evs if e == "progress"]
    assert progress[0].startswith("Finding evidence in your resume for 6 requirements")
    assert "Verifying the evidence and computing the score" in progress
    kind, done = evs[-1]
    assert kind == "done" and done["current_step"] == "scored" and done["fit_result"]["score"] == 70
    assert logged_in.get(f"/sessions/{s['id']}").json()["fit_result"]["score"] == 70  # saved


def test_tailor_stream_reports_each_step(logged_in, use_llm, resume_index):  # noqa: F811
    upload_resume(logged_in)
    s = start_session(logged_in, use_llm)
    use_llm(f"<resume>{TAILORED}</resume>")
    evs = events(logged_in.post(f"/sessions/{s['id']}/tailor", headers=STREAM).text)
    progress = [d["message"] for e, d in evs if e == "progress"]
    assert progress[:2] == ["Writing the tailored draft", "Checking keywords, claims and quantified results"]
    assert evs[-1][0] == "done" and evs[-1][1]["tailored_resume"] == TAILORED


def test_agent_failure_becomes_an_error_event(logged_in, use_llm, resume_index):  # noqa: F811
    upload_resume(logged_in)
    s = start_session(logged_in, use_llm)
    use_llm("", "", "")  # never a resume
    evs = events(logged_in.post(f"/sessions/{s['id']}/tailor", headers=STREAM).text)
    kind, err = evs[-1]
    assert kind == "error" and err["status"] == 502 and "could not produce a tailored resume" in err["detail"]
    assert logged_in.get(f"/sessions/{s['id']}").json()["tailored_resume"] is None


def test_checks_before_the_run_still_return_http_errors(logged_in, use_llm, resume_index):  # noqa: F811
    s = start_session(logged_in, use_llm)
    assert logged_in.post(f"/sessions/{s['id']}/tailor", headers=STREAM).status_code == 400  # no resume
    assert logged_in.post("/sessions/999/fit", headers=STREAM).status_code == 404


def test_json_stays_the_default(logged_in, use_llm, resume_index):  # noqa: F811
    upload_resume(logged_in)
    s = start_session(logged_in, use_llm)
    use_llm(FIT_FINAL)
    r = logged_in.post(f"/sessions/{s['id']}/fit")
    assert r.headers["content-type"].startswith("application/json") and r.json()["fit_result"]["score"] == 70


# ---------- fit re-scored on the tailored resume ----------

def test_tailor_rescores_fit_on_the_tailored_text(logged_in, use_llm, resume_index):  # noqa: F811
    upload_resume(logged_in)
    s = start_session(logged_in, use_llm)
    use_llm(FIT_BEFORE)
    before = logged_in.post(f"/sessions/{s['id']}/fit").json()["fit_result"]["score"]
    llm = use_llm(f"<resume>{TAILORED}</resume>", FIT_FINAL)
    body = logged_in.post(f"/sessions/{s['id']}/tailor").json()
    change = body["tailor_report"]["fit_after"]
    assert change["before"] == before and change["after"] == 70 > before
    assert any(i.startswith("AWS: missing → strong") for i in change["improved"])
    assert "Backend engineer building Python and FastAPI services on AWS" in llm.calls[1][0][1]["content"]
    assert body["agent_trace"]["tailor"][-1]["tool"] == "re-score fit on the tailored resume"
    assert body["fit_result"]["score"] == before  # the original fit is kept as it was


def test_a_failed_rescore_never_loses_the_tailored_resume(logged_in, use_llm, resume_index):  # noqa: F811
    upload_resume(logged_in)
    s = start_session(logged_in, use_llm)
    use_llm(FIT_FINAL)
    logged_in.post(f"/sessions/{s['id']}/fit")
    use_llm(f"<resume>{TAILORED}</resume>")  # nothing scripted for the re-score: it fails
    r = logged_in.post(f"/sessions/{s['id']}/tailor")
    assert r.status_code == 200 and r.json()["tailored_resume"] == TAILORED
    assert r.json()["tailor_report"]["fit_after"] is None


def test_no_rescore_without_an_earlier_fit(logged_in, use_llm, resume_index):  # noqa: F811
    upload_resume(logged_in)
    s = start_session(logged_in, use_llm)
    llm = use_llm(f"<resume>{TAILORED}</resume>")
    body = logged_in.post(f"/sessions/{s['id']}/tailor").json()
    assert len(llm.calls) == 1 and body["tailor_report"]["fit_after"] is None


def test_compare_fit_lists_what_got_better_and_worse():
    req = lambda rid, text, match: {"id": rid, "requirement": text, "importance": "must", "match": match}  # noqa: E731
    before = FitResult(score=50, verdict="Moderate fit", summary="", requirements=[
        req("M1", "AWS", "missing"), req("M2", "Python", "strong"), req("M3", "Docker", "partial")],
        strengths=[], gaps=[], advice=[])
    after = before.model_copy(update={"score": 60, "verdict": "Good fit", "requirements": [
        req("M1", "AWS", "strong"), req("M2", "Python", "partial"), req("M3", "Docker", "partial")]})
    change = compare_fit(before, FitResult.model_validate(after.model_dump()))
    assert (change.before, change.after, change.verdict) == (50, 60, "Good fit")
    assert change.improved == ["AWS: missing → strong"] and change.worse == ["Python: strong → partial"]


def test_text_index_searches_one_text_in_memory():
    index = TextIndex("SKILLS\nPython, FastAPI\n\nEXPERIENCE\nRan AWS infrastructure for payments", FakeEmbedder())
    hits = index.search(0, "AWS infrastructure", 1)
    assert len(hits) == 1 and "AWS" in hits[0].text
    assert TextIndex("", FakeEmbedder()).search(0, "x") == []


# ---------- resume versions ----------

def versions(client, sid):
    r = client.get(f"/sessions/{sid}/resume-versions")
    assert r.status_code == 200, r.text
    return r.json()


def test_every_version_is_kept_and_can_be_restored(logged_in, use_llm, resume_index):  # noqa: F811
    upload_resume(logged_in)
    s = start_session(logged_in, use_llm)
    sid = s["id"]
    use_llm(f"<resume>{TAILORED}</resume>")
    logged_in.post(f"/sessions/{sid}/tailor")
    v1 = versions(logged_in, sid)
    assert [(v["number"], v["source"], v["current"]) for v in v1] == [(1, "tailor", True)]
    assert v1[0]["text"] == TAILORED and v1[0]["coverage"] is not None

    edited = TAILORED + "\n\nINTERESTS\nOpen-source contributor"
    logged_in.patch(f"/sessions/{sid}", json={"tailored_resume": edited})
    logged_in.patch(f"/sessions/{sid}", json={"tailored_resume": edited})  # same text: not a new version
    revised = edited.replace("SUMMARY", "PROFILE")
    use_llm(*[f"<resume>{revised}</resume>"] * 3)
    logged_in.post(f"/sessions/{sid}/tailor", json={"current_resume": edited, "instructions": "Rename summary"})
    listed = versions(logged_in, sid)
    assert [(v["number"], v["source"]) for v in listed] == [(3, "revise"), (2, "edit"), (1, "tailor")]
    assert listed[0]["note"] == "Rename summary" and listed[0]["current"]

    r = logged_in.post(f"/sessions/{sid}/resume-versions/{listed[-1]['id']}/restore")
    assert r.status_code == 200 and r.json()["tailored_resume"] == TAILORED
    assert r.json()["tailor_report"]["revision"] == 0  # the checks that belong to version 1
    top = versions(logged_in, sid)[0]
    assert (top["number"], top["source"], top["note"], top["current"]) == (4, "restore", "Restored version 1", True)


def test_unsaved_edits_become_a_version_before_a_revision(logged_in, use_llm, resume_index):  # noqa: F811
    upload_resume(logged_in)
    s = start_session(logged_in, use_llm)
    use_llm(f"<resume>{TAILORED}</resume>")
    logged_in.post(f"/sessions/{s['id']}/tailor")
    edited = TAILORED + "\n\nINTERESTS\nChess"
    use_llm(*[f"<resume>{edited}</resume>"] * 3)
    logged_in.post(f"/sessions/{s['id']}/tailor", json={"current_resume": edited, "instructions": "Tidy up"})
    assert [v["source"] for v in versions(logged_in, s["id"])] == ["revise", "edit", "tailor"]


def test_sessions_from_before_versions_get_their_text_as_version_one(logged_in, use_llm, db):  # noqa: F811
    s = start_session(logged_in, use_llm)
    row = db.get(JobSession, s["id"])
    row.tailored_resume = TAILORED
    db.commit()
    listed = versions(logged_in, s["id"])
    assert [(v["source"], v["current"]) for v in listed] == [("earlier", True)]


def test_versions_are_private(client, outbox, use_llm, resume_index):  # noqa: F811
    make_user(client, outbox, stay_logged_in=True)
    upload_resume(client)
    s = start_session(client, use_llm)
    use_llm(f"<resume>{TAILORED}</resume>")
    client.post(f"/sessions/{s['id']}/tailor")
    vid = versions(client, s["id"])[0]["id"]
    assert client.post(f"/sessions/{s['id']}/resume-versions/99999/restore").status_code == 404
    client.post("/auth/logout")
    make_user(client, outbox, email="bob@example.com", stay_logged_in=True)
    assert client.get(f"/sessions/{s['id']}/resume-versions").status_code == 404
    assert client.post(f"/sessions/{s['id']}/resume-versions/{vid}/restore").status_code == 404


def test_rescore_reports_one_step_not_the_inner_fit_steps(logged_in, use_llm, resume_index):  # noqa: F811
    upload_resume(logged_in)
    s = start_session(logged_in, use_llm)
    use_llm(FIT_BEFORE)
    logged_in.post(f"/sessions/{s['id']}/fit")
    use_llm(f"<resume>{TAILORED}</resume>", FIT_FINAL)
    evs = events(logged_in.post(f"/sessions/{s['id']}/tailor", headers=STREAM).text)
    progress = [d["message"] for e, d in evs if e == "progress"]
    assert progress[-1] == "Re-scoring your fit on the tailored resume"
    assert not any(m.startswith("Finding evidence") for m in progress)
    assert evs[-1][1]["tailor_report"]["fit_after"]["after"] == 70
