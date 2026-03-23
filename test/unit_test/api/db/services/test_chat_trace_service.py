from types import SimpleNamespace

from api.db.services.chat_trace_service import ChatTraceTurnService


def test_split_think_and_answer():
    think, answer = ChatTraceTurnService.split_think_and_answer("<think>reasoning</think>final answer")
    assert think == "<think>reasoning</think>"
    assert answer == "final answer"


def test_to_training_record():
    row = SimpleNamespace(
        session_id="session-1",
        turn_no=2,
        dialog_id="dialog-1",
        model_input_messages=[{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}],
        knowledge_text="knowledge block",
        think_content="<think>reasoning</think>",
        answer_content="final answer",
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
