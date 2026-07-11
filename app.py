from pathlib import Path

import streamlit as st

from common import (
    accuracy,
    build_bb_card,
    distinguish_zh,
    entries_by_id,
    exam_sets_by_year,
    family_name,
    load_bluebook,
    load_bluebook_group_stats,
    load_bluebook_log,
    load_exam_progress,
    load_exam_questions,
    load_grammar,
    load_listening_questions,
    load_log,
    record_bluebook_answer,
    record_exam_answer,
    save_bluebook_group_stats,
    save_exam_progress,
    synthesize_ja,
)

st.set_page_config(page_title="N2特训 · 真题 / 蓝宝书", page_icon="🈶", layout="wide")
data = load_grammar()
exam_data = load_exam_questions()
log = load_log()
exam_progress = load_exam_progress()
bluebook_data = load_bluebook()
bluebook_log = load_bluebook_log()
bluebook_group_stats = load_bluebook_group_stats()
listening_data = load_listening_questions()
by_id = entries_by_id(data)
bb_by_id = {e["id"]: e for e in bluebook_data["entries"]}

st.title("🈶 N2特训 — 真题 / 蓝宝书")

(
    tab_quiz, tab_bb_quiz, tab_listening,
    tab_bb_browse, tab_search, tab_compare, tab_stats,
) = st.tabs(
    [
        "📝 真题练习", "🎯 蓝宝书测试", "🎧 听力练习",
        "📖 蓝宝书文法", "🔍 検索", "⚖️ 混同比較", "📊 我的进度",
    ]
)

# ==================== 真题部分 ====================

# ---------------- 真题练习 ----------------
with tab_quiz:
    questions = exam_data["questions"]

    if not questions:
        st.write("真题库还是空的。")
    else:
        exam_sets = exam_sets_by_year(questions)
        set_years = sorted(exam_sets.keys())

        with st.expander("各年份最近一次完整练习正确率"):
            for y in set_years:
                acc = exam_progress["set_last_accuracy"].get(y)
                attempts = exam_progress["set_attempts"].get(y, 0)
                if acc is None:
                    st.write(f"- **{y}**：还没完整做过（0%）")
                else:
                    st.write(f"- **{y}**：{acc:.0%}（完整做过 {attempts} 次）")

        def render_quiz_question(q, key_prefix):
            """渲染一道真题的做题UI（题干/选项/提交/解析）。
            返回 (just_answered, next_clicked)：just_answered 只在刚提交的那次 rerun 是 True/False，
            其余时候是 None；next_clicked 表示这次 rerun 里点了"下一题"。"""
            gid = q["grammar_id"]
            grammar_entry = by_id.get(gid)
            is_reorder = q.get("type") == "reorder"
            st.caption(f"{q['year']}　問題{q['question_no']}" + ("　排序题" if is_reorder else ""))
            if is_reorder:
                st.caption("四个选项按顺序能拼成一句完整的话，请判断 ★ 处应该填哪个选项")
            st.write(f"### {q['sentence']}")

            option_labels = [f"{i + 1}. {opt}" for i, opt in enumerate(q["options"])]
            answered_key = f"{key_prefix}_answered"
            picked_key = f"{key_prefix}_picked"
            picked = st.radio(
                "选择最合适的选项", option_labels, index=None, key=f"{key_prefix}_radio",
                disabled=st.session_state.get(answered_key, False),
            )

            just_answered = None
            if (
                st.button("提交答案", key=f"{key_prefix}_submit")
                and picked is not None
                and not st.session_state.get(answered_key, False)
            ):
                picked_index = option_labels.index(picked)
                correct = picked_index == q["answer_index"]
                st.session_state[answered_key] = True
                st.session_state[picked_key] = picked_index
                just_answered = correct
                record_exam_answer(exam_progress, log, q["id"], gid, correct)

            next_clicked = False
            if st.session_state.get(answered_key):
                picked_index = st.session_state[picked_key]
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
                next_clicked = st.button("下一题", key=f"{key_prefix}_next")

            return just_answered, next_clicked

        # ---- 常错题提醒（错2次以上） ----
        weak = [
            (qid, prog) for qid, prog in exam_progress["questions"].items()
            if prog.get("wrong", 0) >= 2 and any(item["id"] == qid for item in questions)
        ]
        if weak:
            weak.sort(key=lambda x: x[1]["wrong"], reverse=True)
            with st.expander(f"⚠️ 常错题提醒（{len(weak)} 题错过2次以上，建议多练习）"):
                for qid, prog in weak:
                    wq = next(item for item in questions if item["id"] == qid)
                    st.write(
                        f"- {wq['year']} 問題{wq['question_no']}："
                        f"错 {prog['wrong']} 次 / 对 {prog.get('correct', 0)} 次 — {wq['sentence'][:30]}…"
                    )

        mode = st.radio("练习模式", ["按套刷题", "错题复习"], horizontal=True, key="quiz_mode")

        if mode == "按套刷题":
            selected_year = st.selectbox("选择一套真题", set_years, key="quiz_set_year")
            set_qs = exam_sets[selected_year]
            attempts = exam_progress["set_attempts"].get(selected_year, 0)
            last_acc = exam_progress["set_last_accuracy"].get(selected_year)
            if attempts:
                st.caption(f"这一套完整做过 {attempts} 次，上次正确率 {last_acc:.0%}")
            else:
                st.caption("这一套还没完整做过")

            if st.session_state.get("quiz_set_year_active") != selected_year:
                st.session_state.quiz_set_year_active = selected_year
                st.session_state.quiz_set_queue = [q["id"] for q in set_qs]
                st.session_state.quiz_set_pos = 0
                st.session_state.quiz_set_wrong = []
                st.session_state.quiz_set_correct_count = 0
                st.session_state.quiz_set_counted = False

            queue = st.session_state.quiz_set_queue
            pos = st.session_state.quiz_set_pos

            if pos >= len(queue):
                total = len(queue)
                correct_n = st.session_state.quiz_set_correct_count
                if not st.session_state.quiz_set_counted:
                    exam_progress["set_attempts"][selected_year] = (
                        exam_progress["set_attempts"].get(selected_year, 0) + 1
                    )
                    exam_progress["set_last_accuracy"][selected_year] = correct_n / total if total else 0
                    save_exam_progress(exam_progress)
                    st.session_state.quiz_set_counted = True

                st.success(f"这套（{selected_year}）完整做完了：共 {total} 题，答对 {correct_n}，答错 {total - correct_n}。")
                if st.session_state.quiz_set_wrong:
                    st.write("**这次答错的题**：")
                    for qid in st.session_state.quiz_set_wrong:
                        wq = next(item for item in questions if item["id"] == qid)
                        st.write(f"- {wq['year']} 問題{wq['question_no']}：{wq['sentence']}")
                if st.button("再做一遍这一套", key="quiz_set_restart"):
                    st.session_state.quiz_set_queue = [q["id"] for q in set_qs]
                    st.session_state.quiz_set_pos = 0
                    st.session_state.quiz_set_wrong = []
                    st.session_state.quiz_set_correct_count = 0
                    st.session_state.quiz_set_counted = False
                    st.rerun()
            else:
                st.caption(f"第 {pos + 1} / {len(queue)} 题")
                qid = queue[pos]
                q = next(item for item in set_qs if item["id"] == qid)
                correct, next_clicked = render_quiz_question(q, key_prefix=f"quiz_set_{qid}")
                if correct is not None:
                    if correct:
                        st.session_state.quiz_set_correct_count += 1
                    else:
                        st.session_state.quiz_set_wrong.append(qid)
                if next_clicked:
                    st.session_state.quiz_set_pos += 1
                    st.rerun()

        else:  # 错题复习
            wrong_qids = [
                qid for qid, prog in exam_progress["questions"].items()
                if prog.get("wrong", 0) > 0 and any(item["id"] == qid for item in questions)
            ]

            if not wrong_qids:
                st.success("目前没有错题记录，太棒了。")
            elif st.session_state.get("quiz_review_queue") is None:
                st.write(f"目前累计有 **{len(wrong_qids)}** 道错题（按错的次数从多到少排列）。")
                if st.button("开始复习错题", key="quiz_review_start"):
                    wrong_qids_sorted = sorted(
                        wrong_qids,
                        key=lambda qid: exam_progress["questions"][qid].get("wrong", 0),
                        reverse=True,
                    )
                    st.session_state.quiz_review_queue = wrong_qids_sorted
                    st.session_state.quiz_review_pos = 0
                    st.session_state.quiz_review_correct = 0
                    st.rerun()
            else:
                rqueue = st.session_state.quiz_review_queue
                rpos = st.session_state.quiz_review_pos

                if rpos >= len(rqueue):
                    st.success(f"错题复习完成：共 {len(rqueue)} 题，答对 {st.session_state.quiz_review_correct} 题。")
                    if st.button("关闭本轮复习", key="quiz_review_close"):
                        st.session_state.quiz_review_queue = None
                        st.rerun()
                else:
                    st.caption(f"复习第 {rpos + 1} / {len(rqueue)} 题")
                    rqid = rqueue[rpos]
                    rq = next(item for item in questions if item["id"] == rqid)
                    correct, next_clicked = render_quiz_question(rq, key_prefix=f"quiz_review_{rqid}")
                    if correct is not None and correct:
                        st.session_state.quiz_review_correct += 1
                    if next_clicked:
                        st.session_state.quiz_review_pos += 1
                        st.rerun()

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
        st.write("还没有测验记录，先去「真题练习」做几题吧。")

# ==================== 蓝宝书部分 ====================

# ---------------- 蓝宝书测试（按5条一组） ----------------
with tab_bb_quiz:
    bb_pool_all = [e for e in bluebook_data["entries"] if e["examples"]]

    if len(bb_pool_all) < 4:
        st.write("蓝宝书条目（带例句的）还不够4条，没法生成选择题，先多记几条吧。")
    else:
        GROUP_SIZE = 5
        groups = {}
        for e in sorted(bb_pool_all, key=lambda e: e.get("no", 0)):
            no = e.get("no", 0)
            start = ((max(no, 1) - 1) // GROUP_SIZE) * GROUP_SIZE + 1
            label = f"{start}-{start + GROUP_SIZE - 1}"
            groups.setdefault(label, []).append(e)
        group_labels = sorted(groups.keys(), key=lambda l: int(l.split("-")[0]))

        with st.expander("各组最近一次完整练习正确率"):
            for label in group_labels:
                stat = bluebook_group_stats.get(label)
                if stat is None:
                    st.write(f"- **{label}**：还没完整做过（0%）")
                else:
                    st.write(f"- **{label}**：{stat['last_accuracy']:.0%}（完整做过 {stat['attempts']} 次）")

        selected_group = st.selectbox("选择一组蓝宝书文法", group_labels, key="bbq_group")
        group_entries = groups[selected_group]

        if st.session_state.get("bbq_group_active") != selected_group:
            st.session_state.bbq_group_active = selected_group
            st.session_state.bbq_queue = [e["id"] for e in group_entries]
            st.session_state.bbq_pos = 0
            st.session_state.bbq_correct = 0
            st.session_state.bbq_wrong = []
            st.session_state.bbq_counted = False
            st.session_state.bbq_card = None

        queue = st.session_state.bbq_queue
        pos = st.session_state.bbq_pos

        if pos >= len(queue):
            if not st.session_state.bbq_counted:
                prev = bluebook_group_stats.get(selected_group, {"attempts": 0})
                bluebook_group_stats[selected_group] = {
                    "attempts": prev.get("attempts", 0) + 1,
                    "last_accuracy": (st.session_state.bbq_correct / len(queue)) if queue else 0,
                }
                save_bluebook_group_stats(bluebook_group_stats)
                st.session_state.bbq_counted = True

            total = len(queue)
            st.success(
                f"这组（{selected_group}）完整做完了：共 {total} 条，"
                f"答对 {st.session_state.bbq_correct}，答错 {total - st.session_state.bbq_correct}。"
            )
            if st.session_state.bbq_wrong:
                st.write("**这次答错的**：")
                for w in st.session_state.bbq_wrong:
                    st.write(f"- {w['pattern']} — {w['meaning_zh']}")
            if st.button("再做一遍这一组", key="bbq_restart"):
                st.session_state.bbq_queue = [e["id"] for e in group_entries]
                st.session_state.bbq_pos = 0
                st.session_state.bbq_correct = 0
                st.session_state.bbq_wrong = []
                st.session_state.bbq_counted = False
                st.session_state.bbq_card = None
                st.rerun()
        else:
            entry_id = queue[pos]
            entry = bb_by_id[entry_id]
            if st.session_state.bbq_card is None:
                st.session_state.bbq_card = build_bb_card(entry, bb_pool_all)
                st.session_state.bbq_answered = False

            card = st.session_state.bbq_card
            st.caption(f"第 {pos + 1} / {len(queue)} 条 · {selected_group} 组 · 第 {entry.get('no', '')} 条")
            st.write(f"### {card['blanked'] if card['blanked'] else card['example']['jp']}")
            st.caption(card["example"]["zh"])

            option_labels = [f"{i + 1}. {opt}" for i, opt in enumerate(card["options"])]
            picked = st.radio(
                "选择最合适的选项" if card["blanked"] else "这句话考查的文法点是？",
                option_labels, index=None, key=f"bbq_radio_{pos}",
                disabled=st.session_state.bbq_answered,
            )
            answer_index = card["options"].index(card["answer_text"])

            if (
                st.button("提交", key=f"bbq_submit_{pos}")
                and picked is not None
                and not st.session_state.bbq_answered
            ):
                picked_index = option_labels.index(picked)
                correct = picked_index == answer_index
                record_bluebook_answer(bluebook_data, bluebook_log, entry, correct)
                st.session_state.bbq_answered = True
                st.session_state.bbq_picked_index = picked_index
                if correct:
                    st.session_state.bbq_correct += 1
                else:
                    st.session_state.bbq_wrong.append(entry)
                st.rerun()

            if st.session_state.bbq_answered:
                picked_index = st.session_state.bbq_picked_index
                if picked_index == answer_index:
                    st.success(f"✅ 正确答案：{card['options'][answer_index]}")
                else:
                    st.error(
                        f"❌ 你选的是 {card['options'][picked_index]}，"
                        f"正确答案是 {card['options'][answer_index]}"
                    )
                if card["blanked"]:
                    st.markdown(f"**完整例句**：{card['example']['jp']}")
                st.markdown(f"**说明**：{entry['meaning_zh']}")
                if entry.get("note"):
                    st.markdown(f"**注意**：{entry['note']}")
                if st.button("下一条", key=f"bbq_next_{pos}"):
                    st.session_state.bbq_pos += 1
                    st.session_state.bbq_card = None
                    st.rerun()

# ---------------- 听力练习 ----------------
with tab_listening:
    sets = listening_data.get("sets", [])
    if not sets:
        st.write("听力题库还是空的。")
    else:
        set_labels = [f"{s['year']} 問題{s['mondai']}" for s in sets]
        selected_idx = st.selectbox(
            "选择一套听力", range(len(sets)), format_func=lambda i: set_labels[i], key="listening_set"
        )
        lset = sets[selected_idx]

        st.caption(lset.get("instruction", ""))
        audio_path = Path(__file__).parent / lset["audio_file"]
        if audio_path.exists():
            st.audio(str(audio_path), format="audio/mp3")
        else:
            st.warning(f"找不到音频文件：{lset['audio_file']}")

        has_answers = all(q.get("answer_index") is not None for q in lset["questions"])
        if not has_answers:
            st.info("这一套还没有录入正确答案，选完之后点提交只会记录你选的，不会判断对错。")

        picks = {}
        for q in lset["questions"]:
            option_labels = [f"{i + 1}. {opt}" for i, opt in enumerate(q["options"])]
            picks[q["no"]] = st.radio(
                f"{q['no']}番", option_labels, index=None, key=f"listening_{lset['id']}_{q['no']}",
            )

        if st.button("提交答案", key=f"listening_{lset['id']}_submit"):
            if any(v is None for v in picks.values()):
                st.warning("还有题目没选。")
            elif not has_answers:
                st.success("已记录你的选择（正确答案还没录入，暂时无法判分）。")
                for q in lset["questions"]:
                    st.write(f"- {q['no']}番：你选的是 {picks[q['no']]}")
            else:
                correct_n = 0
                for q in lset["questions"]:
                    picked_index = int(picks[q["no"]].split(".")[0]) - 1
                    correct = picked_index == q["answer_index"]
                    if correct:
                        correct_n += 1
                        st.success(f"{q['no']}番：✅ 正确")
                    else:
                        st.error(f"{q['no']}番：❌ 正确答案是 {q['answer_index'] + 1}. {q['options'][q['answer_index']]}")
                st.info(f"共 {len(lset['questions'])} 题，答对 {correct_n} 题。")

# ---------------- 蓝宝书文法（浏览） ----------------
with tab_bb_browse:
    query = st.text_input("搜索蓝宝书文法（形式 / 中文意思）", "", key="bluebook_search")
    entries = sorted(bluebook_data["entries"], key=lambda e: e.get("no", 0))
    if query:
        q = query.lower()
        entries = [
            e for e in entries
            if q in e["pattern"].lower() or q in e["meaning_zh"].lower()
        ]

    # 分页：这个 with 代码块每次点击任何标签页的任何按钮都会重新执行一遍（Streamlit 的机制），
    # 不分页会一次性渲染全部条目（每条好几个控件），拖慢整个应用、加重服务器负担。
    PAGE_SIZE = 20
    total_pages = max(1, (len(entries) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = st.number_input("页码", min_value=1, max_value=total_pages, value=1, step=1) if total_pages > 1 else 1
    start = (page - 1) * PAGE_SIZE
    page_entries = entries[start:start + PAGE_SIZE]
    st.caption(f"共 {len(entries)} 条，第 {page} / {total_pages} 页")
    for e in page_entries:
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
