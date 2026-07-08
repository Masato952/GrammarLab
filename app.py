import base64
import io
import json
import os
import random
import re
from datetime import date, timedelta
from pathlib import Path

import requests
import streamlit as st
from gtts import gTTS

DATA_DIR = Path(__file__).parent / "data"
GRAMMAR_FILE = DATA_DIR / "grammar.json"
EXAM_FILE = DATA_DIR / "exam_questions.json"
LOG_FILE = DATA_DIR / "log.json"
BLUEBOOK_FILE = DATA_DIR / "bluebook.json"
BLUEBOOK_LOG_FILE = DATA_DIR / "bluebook_log.json"
EXAM_PROGRESS_FILE = DATA_DIR / "exam_progress.json"

# 简化版莱特纳盒子：答对进下一箱（复习间隔变长），答错打回第0箱（明天重考）
LEITNER_INTERVALS = [1, 2, 4, 7, 15, 30]


def leitner_advance(progress, correct):
    box = 0 if not correct else min(progress.get("box", 0) + 1, len(LEITNER_INTERVALS) - 1)
    progress["box"] = box
    progress["next_review"] = (date.today() + timedelta(days=LEITNER_INTERVALS[box])).isoformat()
    return progress


def _github_config():
    """从 st.secrets（Streamlit Cloud）或环境变量（本地）读取 GitHub 同步配置，
    没配置的话返回 None，调用方就只写本地文件，不报错。"""
    def _get(name, default=None):
        try:
            if name in st.secrets:
                return st.secrets[name]
        except Exception:
            pass
        return os.environ.get(name, default)

    token = _get("GITHUB_TOKEN")
    repo = _get("GITHUB_REPO")
    if not token or not repo:
        return None
    return {"token": token, "repo": repo, "branch": _get("GITHUB_BRANCH", "main")}


def github_commit_file(local_path, repo_relative_path, message):
    """把刚保存到本地的文件同步提交回 GitHub 仓库，这样 Streamlit Cloud 容器休眠重启
    （相当于重新 clone 仓库）之后，今天记录的数据不会丢。本地开发没配置密钥时直接跳过。"""
    config = _github_config()
    if not config:
        return
    try:
        api_url = f"https://api.github.com/repos/{config['repo']}/contents/{repo_relative_path}"
        headers = {
            "Authorization": f"Bearer {config['token']}",
            "Accept": "application/vnd.github+json",
        }
        with open(local_path, "rb") as f:
            content_b64 = base64.b64encode(f.read()).decode("utf-8")

        get_resp = requests.get(api_url, headers=headers, params={"ref": config["branch"]}, timeout=10)
        sha = get_resp.json().get("sha") if get_resp.status_code == 200 else None

        payload = {"message": message, "content": content_b64, "branch": config["branch"]}
        if sha:
            payload["sha"] = sha

        put_resp = requests.put(api_url, headers=headers, json=payload, timeout=10)
        if put_resp.status_code not in (200, 201):
            st.warning(f"同步到 GitHub 失败（{put_resp.status_code}），这次修改先只留在本地。")
    except Exception as exc:
        st.warning(f"同步到 GitHub 出错：{exc}，这次修改先只留在本地。")


def load_grammar():
    with open(GRAMMAR_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_grammar(data):
    with open(GRAMMAR_FILE, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    github_commit_file(GRAMMAR_FILE, "data/grammar.json", "更新 grammar.json")


def load_exam_questions():
    with open(EXAM_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_exam_questions(data):
    with open(EXAM_FILE, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    github_commit_file(EXAM_FILE, "data/exam_questions.json", "更新 exam_questions.json")


def load_log():
    if LOG_FILE.exists():
        with open(LOG_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_log(log):
    with open(LOG_FILE, "w", encoding="utf-8", newline="\n") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)
    github_commit_file(LOG_FILE, "data/log.json", "更新 log.json")


def load_bluebook():
    if BLUEBOOK_FILE.exists():
        with open(BLUEBOOK_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"entries": []}


def save_bluebook(data):
    with open(BLUEBOOK_FILE, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    github_commit_file(BLUEBOOK_FILE, "data/bluebook.json", "更新 bluebook.json")


def load_bluebook_log():
    if BLUEBOOK_LOG_FILE.exists():
        with open(BLUEBOOK_LOG_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_bluebook_log(log):
    with open(BLUEBOOK_LOG_FILE, "w", encoding="utf-8", newline="\n") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)
    github_commit_file(BLUEBOOK_LOG_FILE, "data/bluebook_log.json", "更新 bluebook_log.json")


def load_exam_progress():
    if EXAM_PROGRESS_FILE.exists():
        with open(EXAM_PROGRESS_FILE, encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = {}
    data.setdefault("questions", {})
    data.setdefault("daily", {})
    return data


def save_exam_progress(data):
    with open(EXAM_PROGRESS_FILE, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    github_commit_file(EXAM_PROGRESS_FILE, "data/exam_progress.json", "更新 exam_progress.json")


def record_exam_answer(progress_data, log, qid, gid, correct):
    """答一道真题后：更新旧的 grammar_id 统计（给混同比較/我的进度用），
    同时给这道题单独记录莱特纳盒子进度（给「今日任务」的复习调度用）。"""
    log.setdefault(gid, {"correct": 0, "wrong": 0})
    log[gid]["correct" if correct else "wrong"] += 1
    save_log(log)

    qprogress = progress_data["questions"].setdefault(
        qid, {"correct": 0, "wrong": 0, "box": 0, "attempted": False}
    )
    qprogress["attempted"] = True
    key = "correct" if correct else "wrong"
    qprogress[key] = qprogress.get(key, 0) + 1
    leitner_advance(qprogress, correct)
    save_exam_progress(progress_data)


def record_bluebook_answer(bb_data, bb_log, entry, correct):
    """答一道蓝宝书题后：更新正确率统计，同时推进这条文法的莱特纳盒子进度。"""
    bb_log.setdefault(entry["id"], {"correct": 0, "wrong": 0})
    bb_log[entry["id"]]["correct" if correct else "wrong"] += 1
    save_bluebook_log(bb_log)

    leitner_advance(entry, correct)
    save_bluebook(bb_data)


def exam_sets_by_year(questions):
    years = sorted({q["year"] for q in questions})
    return {y: sorted([q for q in questions if q["year"] == y], key=lambda q: q["question_no"]) for y in years}


def _pattern_variants(pattern):
    """候补挖空文本：按／拆开多个读法，每个读法再生成"去括号整体""去括号符号保留内容"两种写法。"""
    raw = pattern.lstrip("〜").strip()
    candidates = []
    for part in re.split(r"[／/]", raw):
        part = part.strip()
        if not part:
            continue
        removed = re.sub(r"[（(][^）)]*[）)]", "", part).strip()
        merged = re.sub(r"[（）()]", "", part).strip()
        for c in (merged, removed, part):
            if c and c not in candidates:
                candidates.append(c)
    candidates.sort(key=len, reverse=True)
    return candidates


def blank_example(jp, pattern):
    """在例句里找到文法点的实际写法并挖空，找不到就返回 (None, None)。"""
    for candidate in _pattern_variants(pattern):
        idx = jp.find(candidate)
        if idx != -1:
            blanked = jp[:idx] + "（　　）" + jp[idx + len(candidate):]
            return blanked, candidate
    return None, None


def canonical_form(pattern):
    """用于选项展示的干净短形式，如"〜あげく（に）"→"あげく"。"""
    raw = pattern.lstrip("〜").strip()
    first_part = re.split(r"[／/]", raw)[0].strip()
    removed = re.sub(r"[（(][^）)]*[）)]", "", first_part).strip()
    return removed or first_part


def build_bb_card(entry, pool):
    """给一条蓝宝书文法随机挑一句例句，生成挖空句 + 4个选项（1个正确，3个来自 pool 里其他条目）。"""
    example = random.choice(entry["examples"])
    blanked, matched = blank_example(example["jp"], entry["pattern"])
    answer_text = matched if matched else canonical_form(entry["pattern"])
    distractors = random.sample(
        [canonical_form(o["pattern"]) for o in pool if o["id"] != entry["id"]],
        k=3,
    )
    options = distractors + [answer_text]
    random.shuffle(options)
    return {"example": example, "blanked": blanked, "answer_text": answer_text, "options": options}


@st.cache_data(show_spinner="生成语音中…")
def synthesize_ja(text):
    buf = io.BytesIO()
    gTTS(text=text, lang="ja").write_to_fp(buf)
    return buf.getvalue()


def entries_by_id(data):
    return {e["id"]: e for e in data["grammar"]}


def family_name(data, family_id):
    for fam in data["families"]:
        if fam["id"] == family_id:
            return fam["name"]
    return family_id


def accuracy(log, grammar_id):
    stats = log.get(grammar_id, {"correct": 0, "wrong": 0})
    total = stats["correct"] + stats["wrong"]
    if total == 0:
        return None
    return stats["correct"] / total


def distinguish_zh(e):
    return e.get("distinguish_zh") or e.get("distinguish", "")


st.set_page_config(page_title="Grammar Lab", page_icon="🈶", layout="wide")
data = load_grammar()
exam_data = load_exam_questions()
log = load_log()
bluebook_data = load_bluebook()
bluebook_log = load_bluebook_log()
exam_progress = load_exam_progress()
by_id = entries_by_id(data)
bb_by_id = {e["id"]: e for e in bluebook_data["entries"]}
TODAY = date.today().isoformat()

st.title("🈶 Grammar Lab — N2 真题文法特训")

(
    tab_today, tab_bluebook, tab_bluebook_quiz, tab_quiz,
    tab_search, tab_compare, tab_stats,
) = st.tabs(
    [
        "📅 今日任务", "📘 蓝宝书文法", "🎯 蓝宝书测试", "📝 真题小テスト",
        "🔍 検索", "⚖️ 混同比較", "📊 我的进度",
    ]
)

# ---------------- 今日任务 ----------------
with tab_today:
    st.caption("每天固定：5条新蓝宝书文法 + 到期复习的旧文法 + 一套真题問題7。答错的会按遗忘曲线安排更早重考。")

    if "today_mistakes" not in st.session_state:
        st.session_state.today_mistakes = []

    # ---- ① 今日新记录 ----
    st.subheader("① 今日新记录")
    today_added = sum(1 for e in bluebook_data["entries"] if e.get("added_date") == TODAY)
    st.progress(min(today_added / 5, 1.0), text=f"今天已记录 {today_added} / 5 条新蓝宝书文法")
    if today_added < 5:
        st.caption("还没记够5条的话，去「➕ 记录蓝宝书」补上。")

    st.divider()

    # ---- ② 到期复习 · 蓝宝书 ----
    st.subheader("② 到期复习 · 蓝宝书")
    bb_pool = [e for e in bluebook_data["entries"] if e["examples"]]
    due_bb = [e for e in bb_pool if e.get("next_review", TODAY) <= TODAY]

    if len(bb_pool) < 4:
        st.write("蓝宝书条目（带例句的）还不够4条，先去多记几条。")
    elif st.session_state.get("td_bb_queue") is None:
        if not due_bb:
            st.success("今天没有到期需要复习的蓝宝书文法，太棒了。")
        else:
            st.write(f"今天有 **{len(due_bb)}** 条到期需要复习。")
            if st.button("开始复习", key="td_bb_start"):
                st.session_state.td_bb_queue = [e["id"] for e in due_bb]
                st.session_state.td_bb_pos = 0
                st.session_state.td_bb_correct = 0
                st.session_state.td_bb_wrong_list = []
                st.session_state.td_bb_card = None
                st.session_state.td_bb_answered = False
                st.rerun()
    else:
        bb_queue = st.session_state.td_bb_queue
        bb_pos = st.session_state.td_bb_pos
        if bb_pos >= len(bb_queue):
            total = len(bb_queue)
            st.success(
                f"今日蓝宝书复习完成：{total} 张，答对 {st.session_state.td_bb_correct}，"
                f"答错 {total - st.session_state.td_bb_correct}。"
            )
            if st.session_state.td_bb_wrong_list:
                st.write("**答错的**：")
                for w in st.session_state.td_bb_wrong_list:
                    st.write(f"- {w['pattern']} — {w['meaning_zh']}")
            if st.button("关闭本轮复习", key="td_bb_close"):
                st.session_state.td_bb_queue = None
                st.rerun()
        else:
            bb_entry_id = bb_queue[bb_pos]
            bb_entry = bb_by_id[bb_entry_id]
            if st.session_state.td_bb_card is None:
                st.session_state.td_bb_card = build_bb_card(bb_entry, bb_pool)
                st.session_state.td_bb_answered = False

            bb_card = st.session_state.td_bb_card
            st.caption(f"第 {bb_pos + 1} / {len(bb_queue)} 张 · 第 {bb_entry.get('no', '')} 条")
            st.write(f"### {bb_card['blanked'] if bb_card['blanked'] else bb_card['example']['jp']}")
            st.caption(bb_card["example"]["zh"])

            bb_option_labels = [f"{i + 1}. {opt}" for i, opt in enumerate(bb_card["options"])]
            bb_picked = st.radio(
                "选择最合适的选项" if bb_card["blanked"] else "这句话考查的文法点是？",
                bb_option_labels, index=None, key=f"td_bb_radio_{bb_pos}",
                disabled=st.session_state.td_bb_answered,
            )
            bb_answer_index = bb_card["options"].index(bb_card["answer_text"])

            if (
                st.button("提交", key=f"td_bb_submit_{bb_pos}")
                and bb_picked is not None
                and not st.session_state.td_bb_answered
            ):
                bb_picked_index = bb_option_labels.index(bb_picked)
                bb_correct = bb_picked_index == bb_answer_index
                record_bluebook_answer(bluebook_data, bluebook_log, bb_entry, bb_correct)
                st.session_state.td_bb_answered = True
                st.session_state.td_bb_picked_index = bb_picked_index
                if bb_correct:
                    st.session_state.td_bb_correct += 1
                else:
                    st.session_state.td_bb_wrong_list.append(bb_entry)
                    st.session_state.today_mistakes.append({
                        "type": "蓝宝书", "text": bb_entry["pattern"], "detail": bb_entry["meaning_zh"],
                    })
                st.rerun()

            if st.session_state.td_bb_answered:
                bb_picked_index = st.session_state.td_bb_picked_index
                if bb_picked_index == bb_answer_index:
                    st.success(f"✅ 正确答案：{bb_card['options'][bb_answer_index]}")
                else:
                    st.error(
                        f"❌ 你选的是 {bb_card['options'][bb_picked_index]}，"
                        f"正确答案是 {bb_card['options'][bb_answer_index]}"
                    )
                if bb_card["blanked"]:
                    st.markdown(f"**完整例句**：{bb_card['example']['jp']}")
                st.markdown(f"**说明**：{bb_entry['meaning_zh']}")
                if bb_entry.get("note"):
                    st.markdown(f"**注意**：{bb_entry['note']}")
                if st.button("下一张", key=f"td_bb_next_{bb_pos}"):
                    st.session_state.td_bb_pos += 1
                    st.session_state.td_bb_card = None
                    st.rerun()

    st.divider()

    # ---- ③ 今日真题 · 問題7 ----
    st.subheader("③ 今日真题 · 問題7")
    all_questions = exam_data["questions"]
    exam_sets = exam_sets_by_year(all_questions)

    if not all_questions:
        st.write("真题库还是空的，先去「📄 添加真题」加一套。")
    else:
        day_info = exam_progress["daily"].get(TODAY)
        if day_info is None:
            target_year = None
            for y, qs in exam_sets.items():
                if any(not exam_progress["questions"].get(q["id"], {}).get("attempted") for q in qs):
                    target_year = y
                    break
            if target_year:
                day_info = {
                    "year": target_year, "done": False,
                    "queue": [q["id"] for q in exam_sets[target_year]], "pos": 0,
                }
            else:
                day_info = {"year": None, "done": True, "queue": [], "pos": 0}
            exam_progress["daily"][TODAY] = day_info
            save_exam_progress(exam_progress)

        target_year = day_info["year"]

        if target_year and not day_info["done"]:
            year_qs = exam_sets[target_year]
            queue = day_info["queue"]
            pos = day_info["pos"]
            if pos >= len(queue):
                day_info["done"] = True
                save_exam_progress(exam_progress)
                st.rerun()
            else:
                qid = queue[pos]
                q = next(item for item in year_qs if item["id"] == qid)
                gid = q["grammar_id"]
                grammar_entry = by_id.get(gid)
                key_prefix = f"td_exam_{qid}"

                st.caption(f"今天这一套：{target_year}　第 {pos + 1} / {len(queue)} 题")
                st.write(f"### {q['sentence']}")

                option_labels = [f"{i + 1}. {opt}" for i, opt in enumerate(q["options"])]
                picked = st.radio(
                    "选择最合适的选项", option_labels, index=None, key=f"{key_prefix}_radio",
                    disabled=st.session_state.get(f"{key_prefix}_answered", False),
                )

                if (
                    st.button("提交答案", key=f"{key_prefix}_submit")
                    and picked is not None
                    and not st.session_state.get(f"{key_prefix}_answered", False)
                ):
                    picked_index = option_labels.index(picked)
                    correct = picked_index == q["answer_index"]
                    record_exam_answer(exam_progress, log, q["id"], gid, correct)
                    st.session_state[f"{key_prefix}_answered"] = True
                    st.session_state[f"{key_prefix}_picked"] = picked_index
                    if not correct:
                        st.session_state.today_mistakes.append({
                            "type": "真题", "text": f"{q['year']} 問題{q['question_no']}",
                            "detail": q["explanation_zh"],
                        })
                    st.rerun()

                if st.session_state.get(f"{key_prefix}_answered"):
                    picked_index = st.session_state[f"{key_prefix}_picked"]
                    correct_option = q["options"][q["answer_index"]]
                    if picked_index == q["answer_index"]:
                        st.success(f"✅ 回答正确！正确答案是 {q['answer_index'] + 1}. {correct_option}")
                    else:
                        st.error(
                            f"❌ 回答错误。你选的是 {picked_index + 1}. {q['options'][picked_index]}，"
                            f"正确答案是 {q['answer_index'] + 1}. {correct_option}"
                        )
                    st.markdown(f"**解析**：{q['explanation_zh']}")
                    st.markdown(f"**译文**：{q['translation_zh']}")
                    if grammar_entry:
                        st.caption(f"涉及文法点：「{grammar_entry['pattern']}」— {grammar_entry['meaning']}")
                    if st.button("下一题", key=f"{key_prefix}_next"):
                        day_info["pos"] += 1
                        save_exam_progress(exam_progress)
                        st.rerun()

        elif target_year and day_info["done"]:
            year_qs = exam_sets[target_year]
            correct_count = sum(
                1 for q in year_qs
                if exam_progress["questions"].get(q["id"], {}).get("correct", 0) >= 1
            )
            st.success(f"今天这一套（{target_year}）已经做完：{len(year_qs)} 题，答对 {correct_count} 题。明天继续下一套。")

        else:
            st.info("真题库里的每一套都至少做过一遍了，下面是到期需要复习的题目（按遗忘曲线抽取）。")
            due_qs = [
                q for q in all_questions
                if exam_progress["questions"].get(q["id"], {}).get("next_review", TODAY) <= TODAY
            ]
            if st.session_state.get("td_exam_review_queue") is None:
                if not due_qs:
                    st.success("今天也没有到期的真题需要复习。")
                else:
                    st.write(f"今天有 **{len(due_qs)}** 题到期需要复习。")
                    if st.button("开始复习真题", key="td_exam_review_start"):
                        st.session_state.td_exam_review_queue = [q["id"] for q in due_qs]
                        st.session_state.td_exam_review_pos = 0
                        st.session_state.td_exam_review_correct = 0
                        st.rerun()
            else:
                rqueue = st.session_state.td_exam_review_queue
                rpos = st.session_state.td_exam_review_pos
                if rpos >= len(rqueue):
                    st.success(
                        f"今日真题复习完成：{len(rqueue)} 题，答对 {st.session_state.td_exam_review_correct} 题。"
                    )
                    if st.button("关闭本轮复习", key="td_exam_review_close"):
                        st.session_state.td_exam_review_queue = None
                        st.rerun()
                else:
                    rqid = rqueue[rpos]
                    rq = next(item for item in all_questions if item["id"] == rqid)
                    rgid = rq["grammar_id"]
                    rgrammar_entry = by_id.get(rgid)
                    rkey_prefix = f"td_exam_review_{rqid}"

                    st.caption(f"复习第 {rpos + 1} / {len(rqueue)} 题 · {rq['year']} 問題{rq['question_no']}")
                    st.write(f"### {rq['sentence']}")

                    roption_labels = [f"{i + 1}. {opt}" for i, opt in enumerate(rq["options"])]
                    rpicked = st.radio(
                        "选择最合适的选项", roption_labels, index=None, key=f"{rkey_prefix}_radio",
                        disabled=st.session_state.get(f"{rkey_prefix}_answered", False),
                    )

                    if (
                        st.button("提交答案", key=f"{rkey_prefix}_submit")
                        and rpicked is not None
                        and not st.session_state.get(f"{rkey_prefix}_answered", False)
                    ):
                        rpicked_index = roption_labels.index(rpicked)
                        rcorrect = rpicked_index == rq["answer_index"]
                        record_exam_answer(exam_progress, log, rq["id"], rgid, rcorrect)
                        st.session_state[f"{rkey_prefix}_answered"] = True
                        st.session_state[f"{rkey_prefix}_picked"] = rpicked_index
                        if rcorrect:
                            st.session_state.td_exam_review_correct += 1
                        else:
                            st.session_state.today_mistakes.append({
                                "type": "真题复习", "text": f"{rq['year']} 問題{rq['question_no']}",
                                "detail": rq["explanation_zh"],
                            })
                        st.rerun()

                    if st.session_state.get(f"{rkey_prefix}_answered"):
                        rpicked_index = st.session_state[f"{rkey_prefix}_picked"]
                        rcorrect_option = rq["options"][rq["answer_index"]]
                        if rpicked_index == rq["answer_index"]:
                            st.success(f"✅ 回答正确！正确答案是 {rq['answer_index'] + 1}. {rcorrect_option}")
                        else:
                            st.error(
                                f"❌ 回答错误。你选的是 {rpicked_index + 1}. {rq['options'][rpicked_index]}，"
                                f"正确答案是 {rq['answer_index'] + 1}. {rcorrect_option}"
                            )
                        st.markdown(f"**解析**：{rq['explanation_zh']}")
                        st.markdown(f"**译文**：{rq['translation_zh']}")
                        if rgrammar_entry:
                            st.caption(f"涉及文法点：「{rgrammar_entry['pattern']}」— {rgrammar_entry['meaning']}")
                        if st.button("下一题", key=f"{rkey_prefix}_next"):
                            st.session_state.td_exam_review_pos += 1
                            st.rerun()

    st.divider()

    # ---- ④ 今天的错题分析 ----
    st.subheader("④ 今天的错题分析")
    if st.session_state.today_mistakes:
        by_type = {}
        for m in st.session_state.today_mistakes:
            by_type.setdefault(m["type"], []).append(m)
        for t, items in by_type.items():
            st.write(f"**{t}**（{len(items)} 处）")
            for it in items:
                with st.expander(it["text"]):
                    st.write(it["detail"])
    else:
        st.caption("这次打开还没有错题记录，做完上面的任务后这里会自动列出来。")

# ---------------- 検索 ----------------
with tab_search:
    query = st.text_input("输入文法、假名或中文意思进行搜索", "")
    results = data["grammar"]
    if query:
        q = query.lower()
        results = [
            e
            for e in data["grammar"]
            if q in e["pattern"].lower() or q in e["meaning"].lower()
        ]
    st.caption(f"共 {len(results)} 条")
    for e in results:
        with st.expander(f"{e['pattern']} — {e['meaning']}"):
            st.write(f"**接续**：{e['connection']}")
            st.write(f"**语体**：{e['formality']}")
            st.write(f"**所属家族**：{family_name(data, e['family'])}")
            st.write(f"**出处**：{e.get('source', '')}")
            if e.get("confusable"):
                confusable_patterns = [
                    by_id[c]["pattern"] for c in e["confusable"] if c in by_id
                ]
                st.write(f"**易混淆**：{'、'.join(confusable_patterns)}")
            st.write(f"**区分要点（中文）**：{distinguish_zh(e)}")
            for ex in e["examples"]:
                st.write(f"- {ex['jp']}　（{ex['zh']}）")
            acc = accuracy(log, e["id"])
            if acc is not None:
                st.progress(acc, text=f"测验正确率 {acc:.0%}")

# ---------------- 混同比較 ----------------
with tab_compare:
    fam_options = {fam["id"]: fam["name"] for fam in data["families"]}
    fam_id = st.selectbox(
        "选择一个文法家族查看对比", options=list(fam_options.keys()),
        format_func=lambda x: fam_options[x],
    )
    members = [by_id[m] for m in next(f for f in data["families"] if f["id"] == fam_id)["members"] if m in by_id]
    cols = st.columns(len(members))
    for col, e in zip(cols, members):
        with col:
            st.subheader(e["pattern"])
            st.write(f"**意味**：{e['meaning']}")
            st.write(f"**接续**：{e['connection']}")
            st.write(f"**语体**：{e['formality']}")
            st.write(f"**区分（中文）**：{distinguish_zh(e)}")
            for ex in e["examples"]:
                st.caption(f"{ex['jp']}（{ex['zh']}）")

# ---------------- 真题小テスト ----------------
with tab_quiz:
    questions = exam_data["questions"]

    if not questions:
        st.write("真题库还是空的，先去「📄 添加真题」加几题吧。")
    else:
        years = ["全部"] + sorted({q["year"] for q in questions})
        categories = ["全部"] + sorted({q.get("category", "文法") for q in questions})
        col_year, col_cat = st.columns(2)
        with col_year:
            year_filter = st.selectbox("按年份/试卷筛选", years, key="filter_year")
        with col_cat:
            cat_filter = st.selectbox("按分类筛选（文法/词汇……）", categories, key="filter_category")

        filtered = [
            q for q in questions
            if (year_filter == "全部" or q["year"] == year_filter)
            and (cat_filter == "全部" or q.get("category", "文法") == cat_filter)
        ]

    if questions and not filtered:
        st.warning("这个筛选条件下还没有题目。")
    elif questions:
        if "quiz_qid" not in st.session_state:
            st.session_state.quiz_qid = None
            st.session_state.answered = False
            st.session_state.picked_index = None
            st.session_state.quiz_filter = None

        def new_question(pool):
            weights = [
                1.0 if accuracy(log, q["grammar_id"]) is None
                else (1.1 - accuracy(log, q["grammar_id"]))
                for q in pool
            ]
            q = random.choices(pool, weights=weights, k=1)[0]
            st.session_state.quiz_qid = q["id"]
            st.session_state.answered = False
            st.session_state.picked_index = None
            st.session_state.quiz_filter = (year_filter, cat_filter)

        current_filter = (year_filter, cat_filter)
        need_new = (
            st.session_state.quiz_qid is None
            or st.session_state.quiz_filter != current_filter
            or not any(q["id"] == st.session_state.quiz_qid for q in filtered)
        )
        if need_new:
            new_question(filtered)

        q = next(item for item in questions if item["id"] == st.session_state.quiz_qid)
        gid = q["grammar_id"]
        grammar_entry = by_id.get(gid)

        is_reorder = q.get("type") == "reorder"
        st.caption(f"{q['year']}　問題{q['question_no']}" + ("　排序题" if is_reorder else ""))
        if is_reorder:
            st.caption("四个选项按顺序能拼成一句完整的话，请判断 ★ 处应该填哪个选项")
        st.write(f"### {q['sentence']}")

        option_labels = [f"{i + 1}. {opt}" for i, opt in enumerate(q["options"])]
        picked = st.radio(
            "选择最合适的选项", option_labels, index=None, key="quiz_radio",
            disabled=st.session_state.answered,
        )

        if st.button("提交答案") and picked is not None and not st.session_state.answered:
            st.session_state.answered = True
            st.session_state.picked_index = option_labels.index(picked)
            correct = st.session_state.picked_index == q["answer_index"]
            record_exam_answer(exam_progress, log, q["id"], gid, correct)
            st.rerun()

        if st.session_state.answered:
            picked_index = st.session_state.picked_index
            correct_option = q["options"][q["answer_index"]]
            if picked_index == q["answer_index"]:
                st.success(f"✅ 回答正确！正确答案是 {q['answer_index'] + 1}. {correct_option}")
            else:
                st.error(
                    f"❌ 回答错误。你选的是 {picked_index + 1}. {q['options'][picked_index]}，"
                    f"正确答案是 {q['answer_index'] + 1}. {correct_option}"
                )
            if is_reorder and q.get("full_order"):
                full_sentence = "".join(q["options"][n - 1] for n in q["full_order"])
                st.markdown(f"**完整语序**：{full_sentence}")
            st.markdown(f"**解析**：{q['explanation_zh']}")
            st.markdown(f"**译文**：{q['translation_zh']}")
            if grammar_entry:
                st.caption(f"涉及文法点：「{grammar_entry['pattern']}」— {grammar_entry['meaning']}")

            if st.button("下一题"):
                new_question(filtered)
                st.rerun()

# ---------------- 我的进度 ----------------
with tab_stats:
    total_correct = sum(stats["correct"] for stats in log.values())
    total_wrong = sum(stats["wrong"] for stats in log.values())
    total_attempts = total_correct + total_wrong
    if total_attempts:
        col_correct, col_wrong, col_acc = st.columns(3)
        col_correct.metric("累计答对", total_correct)
        col_wrong.metric("累计答错", total_wrong)
        col_acc.metric("正确率", f"{total_correct / total_attempts:.0%}")

    fam_acc = {}
    for fam in data["families"]:
        accs = [accuracy(log, m) for m in fam["members"] if accuracy(log, m) is not None]
        if accs:
            fam_acc[fam["name"]] = sum(accs) / len(accs)
    if fam_acc:
        st.bar_chart(fam_acc)
        weakest = min(fam_acc, key=fam_acc.get)
        st.info(f"目前最薄弱的家族：**{weakest}**（正确率 {fam_acc[weakest]:.0%}），小测验里会优先出这一组的题。")
    else:
        st.write("还没有测验记录，先去「真题小テスト」做几题吧。")

# ---------------- 蓝宝书文法（浏览） ----------------
with tab_bluebook:
    st.caption("这里单独放蓝宝书上的文法条目，和真题库互不影响。")

    today = date.today().isoformat()
    today_count = sum(1 for e in bluebook_data["entries"] if e.get("added_date") == today)
    daily_goal = st.number_input("每日目标（条）", min_value=1, max_value=50, value=5, step=1)
    st.progress(
        min(today_count / daily_goal, 1.0),
        text=f"今天已记录 {today_count} / {daily_goal} 条",
    )
    st.write(f"蓝宝书条目共 **{len(bluebook_data['entries'])}** 条")

    query = st.text_input("搜索蓝宝书文法（形式 / 中文意思）", "", key="bluebook_search")
    entries = sorted(bluebook_data["entries"], key=lambda e: e.get("no", 0))
    if query:
        q = query.lower()
        entries = [
            e for e in entries
            if q in e["pattern"].lower() or q in e["meaning_zh"].lower()
        ]
    st.caption(f"共 {len(entries)} 条")
    for e in entries:
        with st.expander(f"{e.get('no', '')}. {e['pattern']} — {e['meaning_zh']}"):
            st.write(f"**接续**：{e['connection']}")
            st.write(f"**说明**：{e['meaning_zh']}")
            for i, ex in enumerate(e["examples"]):
                src = f"【{ex['source']}】" if ex.get("source") else ""
                col_text, col_play = st.columns([9, 1])
                with col_text:
                    st.write(f"- {ex['jp']}{src}")
                    st.caption(ex["zh"])
                with col_play:
                    audio_key = f"tts_audio_{e['id']}_{i}"
                    if st.button("🔊", key=f"tts_btn_{e['id']}_{i}"):
                        st.session_state[audio_key] = synthesize_ja(ex["jp"])
                if audio_key in st.session_state:
                    st.audio(st.session_state[audio_key], format="audio/mp3")
            if e.get("note"):
                st.write(f"**注意**：{e['note']}")

            st.caption(f"添加日期：{e.get('added_date', '')}")
            acc = accuracy(bluebook_log, e["id"])
            if acc is not None:
                st.progress(acc, text=f"测验正确率 {acc:.0%}")

# ---------------- 蓝宝书测试 ----------------
with tab_bluebook_quiz:
    bb_entries = [e for e in bluebook_data["entries"] if e["examples"]]

    if len(bb_entries) < 4:
        st.write("蓝宝书条目（带例句的）还不够4条，没法生成选择题，先去「➕ 记录蓝宝书」多加几条吧。")
    else:
        if "bb_quiz_id" not in st.session_state:
            st.session_state.bb_quiz_id = None
            st.session_state.bb_answered = False
            st.session_state.bb_picked_index = None
            st.session_state.bb_options = None
            st.session_state.bb_example = None
            st.session_state.bb_blanked = None
            st.session_state.bb_answer_text = None

        def bb_new_question():
            weights = [
                1.0 if accuracy(bluebook_log, e["id"]) is None
                else (1.1 - accuracy(bluebook_log, e["id"]))
                for e in bb_entries
            ]
            e = random.choices(bb_entries, weights=weights, k=1)[0]
            card = build_bb_card(e, bb_entries)
            st.session_state.bb_quiz_id = e["id"]
            st.session_state.bb_answered = False
            st.session_state.bb_picked_index = None
            st.session_state.bb_options = card["options"]
            st.session_state.bb_example = card["example"]
            st.session_state.bb_blanked = card["blanked"]
            st.session_state.bb_answer_text = card["answer_text"]

        if st.session_state.bb_quiz_id is None or st.session_state.bb_quiz_id not in bb_by_id:
            bb_new_question()

        e = bb_by_id[st.session_state.bb_quiz_id]
        example = st.session_state.bb_example
        options = st.session_state.bb_options
        answer_index = options.index(st.session_state.bb_answer_text)
        is_blanked = st.session_state.bb_blanked is not None

        st.caption(f"第 {e.get('no', '')} 条 · 蓝宝书文法测试" + ("" if is_blanked else "（未能自动挖空）"))
        st.write(f"### {st.session_state.bb_blanked if is_blanked else example['jp']}")
        st.caption(example["zh"])

        option_labels = [f"{i + 1}. {opt}" for i, opt in enumerate(options)]
        picked = st.radio(
            "选择最合适的选项" if is_blanked else "这句话考查的文法点是？",
            option_labels, index=None, key="bb_quiz_radio",
            disabled=st.session_state.bb_answered,
        )

        if st.button("提交答案", key="bb_submit") and picked is not None and not st.session_state.bb_answered:
            st.session_state.bb_answered = True
            st.session_state.bb_picked_index = option_labels.index(picked)
            correct = st.session_state.bb_picked_index == answer_index
            record_bluebook_answer(bluebook_data, bluebook_log, e, correct)
            st.rerun()

        if st.session_state.bb_answered:
            picked_index = st.session_state.bb_picked_index
            if picked_index == answer_index:
                st.success(f"✅ 回答正确！正确答案是 {answer_index + 1}. {options[answer_index]}")
            else:
                st.error(
                    f"❌ 回答错误。你选的是 {picked_index + 1}. {options[picked_index]}，"
                    f"正确答案是 {answer_index + 1}. {options[answer_index]}"
                )
            if is_blanked:
                st.markdown(f"**完整例句**：{example['jp']}")
            st.markdown(f"**说明**：{e['meaning_zh']}")
            if e.get("note"):
                st.markdown(f"**注意**：{e['note']}")

            if st.button("下一题", key="bb_next"):
                bb_new_question()
                st.rerun()
