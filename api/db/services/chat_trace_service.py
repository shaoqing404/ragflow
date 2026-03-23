#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
from copy import deepcopy
import re

from api.db.db_models import ChatTraceTurn
from api.db.services.common_service import CommonService


class ChatTraceTurnService(CommonService):
    model = ChatTraceTurn

    @staticmethod
    def sanitize_reference(snapshot):
        if not isinstance(snapshot, dict):
            return snapshot
        sanitized = deepcopy(snapshot)
        for chunk in sanitized.get("chunks", []) or []:
            if isinstance(chunk, dict):
                chunk.pop("vector", None)
        return sanitized

    @staticmethod
    def split_think_and_answer(full_response: str) -> tuple[str, str]:
        text = full_response or ""
        match = re.match(r"(?s)^(<think>.*?</think>)(.*)$", text)
        if not match:
            return "", text
        return match.group(1), match.group(2)

    @classmethod
    def create_pending_turn(
        cls,
        *,
        tenant_id,
        dialog_id,
        session_id,
        source,
        turn_no,
        user_message_id,
        assistant_message_id,
        request_question,
        request_payload,
        history_snapshot,
    ):
        return cls.insert(
            tenant_id=tenant_id,
            dialog_id=dialog_id,
            session_id=session_id,
            source=source,
            turn_no=turn_no,
            user_message_id=user_message_id,
            assistant_message_id=assistant_message_id,
            request_question=request_question,
            request_payload=request_payload,
            history_snapshot=history_snapshot,
            status="running",
        )

    @classmethod
    def update_turn(cls, trace_turn_id, **fields):
        payload = {k: v for k, v in fields.items() if v is not None}
        if not payload:
            return 0
        return cls.update_by_id(trace_turn_id, payload)

    @classmethod
    def finalize_turn(
        cls,
        trace_turn_id,
        *,
        full_response,
        response_reference=None,
        prompt_snapshot=None,
        timing_metrics=None,
    ):
        think_content, answer_content = cls.split_think_and_answer(full_response)
        return cls.update_by_id(
            trace_turn_id,
            {
                "think_content": think_content,
                "answer_content": answer_content,
                "full_response": full_response,
                "response_reference": cls.sanitize_reference(response_reference or {}),
                "prompt_snapshot": prompt_snapshot,
                "timing_metrics": timing_metrics or {},
                "status": "done",
                "error_message": "",
            },
        )

    @classmethod
    def mark_error(cls, trace_turn_id, error_message):
        return cls.update_by_id(
            trace_turn_id,
            {
                "status": "error",
                "error_message": str(error_message),
            },
        )

    @classmethod
    def to_training_record(cls, trace_turn: ChatTraceTurn):
        return {
            "session_id": trace_turn.session_id,
            "turn_no": trace_turn.turn_no,
            "dialog_id": trace_turn.dialog_id,
            "messages": trace_turn.model_input_messages or [],
            "knowledge": trace_turn.knowledge_text or "",
            "think": trace_turn.think_content or "",
            "answer": trace_turn.answer_content or "",
        }
