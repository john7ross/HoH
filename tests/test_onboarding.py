import unittest

from llm_harness.onboarding import ROLE_PRESETS, onboarding_progress, role_preset


class OnboardingTests(unittest.TestCase):
    def test_popular_presets_cover_all_three_roles_and_required_agents(self):
        preset = role_preset("codex_claude_deepseek")

        self.assertEqual(preset.supervisor.agent, "codex-acp")
        self.assertEqual(preset.worker.agent, "claude-acp")
        self.assertEqual(preset.critic.agent, "qwen-code")  # type: ignore[union-attr]
        self.assertEqual(preset.critic.model, "deepseek-chat")  # type: ignore[union-attr]
        self.assertEqual(preset.required_agents, ("codex-acp", "claude-acp", "qwen-code"))
        self.assertEqual(len(ROLE_PRESETS), 3)

    def test_progress_requires_the_selected_agents_to_be_on_this_machine(self):
        profile = role_preset("codex_claude_deepseek").profile()
        catalog = {
            "agents": [
                {"name": "codex-acp", "available": True},
                {"name": "claude-acp", "available": True},
                {"name": "qwen-code", "available": True},
            ]
        }

        progress = onboarding_progress(profile, catalog)

        self.assertTrue(progress.complete)
        self.assertEqual(progress.missing_agents, ())

    def test_missing_agents_block_setup_and_are_named(self):
        profile = role_preset("codex_claude_deepseek").profile()

        blocked = onboarding_progress(profile, {"agents": [{"name": "codex-acp", "available": True}]})

        self.assertFalse(blocked.complete)
        self.assertEqual(blocked.missing_agents, ("worker", "critic"))

    def test_setup_does_not_claim_to_know_whether_a_vendor_login_works(self):
        # Nothing can know that without calling the agent, so progress must not
        # depend on it. The first real task is what answers the question.
        profile = role_preset("codex_claude_deepseek").profile()
        catalog = {
            "agents": [
                {"name": "codex-acp", "available": True},
                {"name": "claude-acp", "available": True},
                {"name": "qwen-code", "available": True},
            ]
        }

        progress = onboarding_progress(profile, catalog)

        self.assertFalse(hasattr(progress, "live_certified"))
        self.assertTrue(progress.complete)

    def test_without_a_saved_profile_setup_is_not_complete(self):
        progress = onboarding_progress(None, {"agents": []})

        self.assertFalse(progress.complete)
        self.assertTrue(progress.project_ready)


if __name__ == "__main__":
    unittest.main()
