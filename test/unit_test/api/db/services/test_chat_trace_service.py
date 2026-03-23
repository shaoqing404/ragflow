from types import SimpleNamespace

from api.db.services.chat_trace_service import ChatTraceTurnService


def test_split_think_and_answer():
    think, answer = ChatTraceTurnService.split_think_and_answer("<think>reasoning</think>final answer")
    assert think == "<think>reasoning</think>"
    assert answer == "final answer"


def test_to_training_record():
    row = SimpleNamespace(
        id="trace-1",
        session_id="session-1",
        turn_no=2,
        dialog_id="dialog-1",
        source="chatbot",
        llm_name="teacher",
        llm_factory="OpenAI",
        history_snapshot=[],
        retrieval_snapshot={},
        response_reference={},
        status="done",
        model_input_messages=[{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}],
        knowledge_text="knowledge block",
        think_content="<think>reasoning</think>",
        answer_content="final answer",
        full_response="<think>reasoning</think>final answer",
    )
    assert ChatTraceTurnService.to_training_record(row) == {
        "session_id": "session-1",
        "turn_no": 2,
        "dialog_id": "dialog-1",
        "messages": [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}],
        "knowledge": "knowledge block",
        "think": "<think>reasoning</think>",
        "answer": "final answer",
    }


def test_to_sft_answer_record():
    row = SimpleNamespace(
        id="trace-1",
        session_id="session-1",
        turn_no=2,
        dialog_id="dialog-1",
        source="chatbot",
        llm_name="teacher",
        llm_factory="OpenAI",
        history_snapshot=[],
        retrieval_snapshot={"chunks": []},
        response_reference={"chunks": []},
        status="done",
        model_input_messages=[{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}],
        knowledge_text="knowledge block",
        think_content="<think>reasoning</think>",
        answer_content="final answer",
        full_response="<think>reasoning</think>final answer",
    )
    record = ChatTraceTurnService.to_sft_answer_record(row)
    assert record["assistant"] == "final answer"
    assert record["messages"] == [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
    assert record["metadata"]["has_think"] is True
    assert record["metadata"]["knowledge"] == "knowledge block"


def test_to_dpo_and_grpo_seed_records():
    row = SimpleNamespace(
        id="trace-1",
        session_id="session-1",
        turn_no=2,
        dialog_id="dialog-1",
        source="chatbot",
        llm_name="teacher",
        llm_factory="OpenAI",
        history_snapshot=[],
        retrieval_snapshot={},
        response_reference={},
        status="done",
        model_input_messages=[{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}],
        knowledge_text="knowledge block",
        think_content="<think>reasoning</think>",
        answer_content="final answer",
        full_response="<think>reasoning</think>final answer",
    )
    dpo_record = ChatTraceTurnService.to_dpo_seed_record(row)
    assert dpo_record["chosen"] == "final answer"
    assert dpo_record["chosen_cot"] == "<think>reasoning</think>final answer"
    assert dpo_record["rejected"] is None

    grpo_record = ChatTraceTurnService.to_grpo_seed_record(row)
    assert grpo_record["teacher"]["answer"] == "final answer"
    assert grpo_record["teacher"]["think"] == "<think>reasoning</think>"
    assert grpo_record["candidates"] == []
