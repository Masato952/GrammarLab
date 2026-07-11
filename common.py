import base64
import io
import json
import os
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


def github_fetch_file(repo_relative_path):
    """从 GitHub 仓库拉取这个文件的最新内容。Streamlit Cloud 容器可能长时间不重启，
    本地 clone 会跟仓库脱节；如果每次都只信本地文件，容器自己保存时就会用旧数据覆盖掉
    仓库里更新的内容。拿不到（没配密钥/网络问题/文件还不存在）就返回 None，调用方回退到本地文件。"""
    config = _github_config()
    if not config:
        return None
    try:
        api_url = f"https://api.github.com/repos/{config['repo']}/contents/{repo_relative_path}"
        headers = {
            "Authorization": f"Bearer {config['token']}",
            "Accept": "application/vnd.github.raw+json",
        }
        resp = requests.get(api_url, headers=headers, params={"ref": config["branch"]}, timeout=10)
        if resp.status_code == 200 and resp.text.strip():
            return resp.text
    except Exception:
        pass
    return None


def _safe_json_load(path, default, repo_relative_path=None):
    """读取一个数据文件。如果配置了 GitHub 同步，这个浏览器 session 里第一次读取该文件时，
    会先尝试从 GitHub 拉最新版本覆盖本地文件（只做一次，避免每次点击都请求 API），
    保证不会因为容器长时间没重启、本地缓存脱节而用旧数据覆盖仓库。
    文件不存在、为空或损坏时不让整个应用崩掉，退回默认值并提示一下。"""
    if repo_relative_path:
        session_key = f"_synced_{repo_relative_path}"
        if not st.session_state.get(session_key):
            remote_content = github_fetch_file(repo_relative_path)
            if remote_content is not None:
                try:
                    with open(path, "w", encoding="utf-8", newline="\n") as f:
                        f.write(remote_content)
                except Exception:
                    pass
            st.session_state[session_key] = True

    if not path.exists():
        return default
    try:
        with open(path, encoding="utf-8") as f:
            content = f.read()
        if not content.strip():
            st.warning(f"{path.name} 是空的，先用默认数据启动，请稍后刷新页面。")
            return default
        return json.loads(content)
    except json.JSONDecodeError:
        st.warning(f"{path.name} 内容损坏，先用默认数据启动，请稍后刷新页面。")
        return default


def load_grammar():
    return _safe_json_load(GRAMMAR_FILE, {"families": [], "grammar": []}, "data/grammar.json")


def save_grammar(data):
    with open(GRAMMAR_FILE, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    github_commit_file(GRAMMAR_FILE, "data/grammar.json", "更新 grammar.json")


def load_exam_questions():
    return _safe_json_load(EXAM_FILE, {"questions": []}, "data/exam_questions.json")


def save_exam_questions(data):
    with open(EXAM_FILE, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    github_commit_file(EXAM_FILE, "data/exam_questions.json", "更新 exam_questions.json")


def load_log():
    return _safe_json_load(LOG_FILE, {}, "data/log.json")


def save_log(log):
    with open(LOG_FILE, "w", encoding="utf-8", newline="\n") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)
    github_commit_file(LOG_FILE, "data/log.json", "更新 log.json")


def load_bluebook():
    return _safe_json_load(BLUEBOOK_FILE, {"entries": []}, "data/bluebook.json")


def save_bluebook(data):
    with open(BLUEBOOK_FILE, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    github_commit_file(BLUEBOOK_FILE, "data/bluebook.json", "更新 bluebook.json")


def load_bluebook_log():
    return _safe_json_load(BLUEBOOK_LOG_FILE, {}, "data/bluebook_log.json")


def save_bluebook_log(log):
    with open(BLUEBOOK_LOG_FILE, "w", encoding="utf-8", newline="\n") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)
    github_commit_file(BLUEBOOK_LOG_FILE, "data/bluebook_log.json", "更新 bluebook_log.json")


def load_exam_progress():
    data = _safe_json_load(EXAM_PROGRESS_FILE, {}, "data/exam_progress.json")
    data.setdefault("questions", {})
    data.setdefault("daily", {})
    data.setdefault("set_attempts", {})
    return data


def save_exam_progress(data):
    with open(EXAM_PROGRESS_FILE, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    github_commit_file(EXAM_PROGRESS_FILE, "data/exam_progress.json", "更新 exam_progress.json")


def record_exam_answer(progress_data, log, qid, gid, correct):
    """答一道真题后：更新旧的 grammar_id 统计（给混同比較/我的进度用），
    同时给这道题单独记录莱特纳盒子进度（给复习调度用）。"""
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
    import random
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
