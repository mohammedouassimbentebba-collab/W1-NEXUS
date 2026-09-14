import tempfile
import unittest
from pathlib import Path

from w1cip.conversation_context import ConversationStore


class ConversationContextTests(unittest.TestCase):
    def test_full_history_is_saved_but_context_is_token_bounded(self):
        with tempfile.TemporaryDirectory() as td:
            with ConversationStore(Path(td) / 'conversation.sqlite3') as store:
                for i in range(20):
                    store.append_message('chat-one', role='user' if i % 2 == 0 else 'assistant', content=(f'turn {i} ' + 'detail ' * 80))
                messages = store.messages('chat-one')
                self.assertEqual(len(messages), 20)
                pack = store.build_context('chat-one', token_budget=512)
                self.assertLessEqual(pack.estimated_tokens, 512)
                self.assertGreater(pack.full_history_tokens, pack.estimated_tokens)
                self.assertGreater(pack.estimated_tokens_saved, 0)
                self.assertGreater(pack.omitted_messages, 0)

    def test_explicit_summary_replaces_old_exact_turns(self):
        with tempfile.TemporaryDirectory() as td:
            with ConversationStore(Path(td) / 'conversation.sqlite3') as store:
                for i in range(6):
                    store.append_message('chat-two', role='user', content=f'message {i} about pump requirements')
                store.record_summary('chat-two', start_sequence=1, end_sequence=4, summary='Earlier turns established the pump requirements.', source='explicit')
                pack = store.build_context('chat-two', token_budget=256)
                self.assertIsNotNone(pack.summary)
                self.assertTrue(all(m.sequence > 4 for m in pack.recent_messages))
                self.assertEqual(pack.summary.end_sequence, 4)

    def test_model_output_is_not_long_term_memory_by_default(self):
        with tempfile.TemporaryDirectory() as td:
            with ConversationStore(Path(td) / 'conversation.sqlite3') as store:
                result = store.append_message('chat-three', role='assistant', content='candidate answer', model_id='model-a')
                self.assertEqual(result.model_id, 'model-a')
                self.assertEqual(len(store.messages('chat-three')), 1)
