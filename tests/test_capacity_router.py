import tempfile
import unittest
from pathlib import Path

from w1cip.capacity_router import (
    AdaptiveCapacityRouter,
    CapacityObservation,
    CapacityStore,
    RoutingPolicy,
    run_capacity_router_benchmark,
)
from w1cip.model_access import ModelProfile
from w1cip.orchestrator import CompiledTask


def profile(model_id, provider, quality, *, source='paid', roles=('producer',), terms='official', access='byok_api'):
    return ModelProfile(
        model_id=model_id,
        display_name=model_id,
        provider_id=provider,
        connector_type='openai_compatible',
        connector_resource_id=f'resource-{model_id}',
        model_name=model_id,
        access_mode=access,
        privacy_mode='local' if access == 'local_endpoint' else 'provider_cloud',
        capabilities={'coding': quality, 'general': quality, 'challenger': quality, 'synthesizer': quality, 'verifier': quality},
        roles=roles,
        domains=('coding',),
        endpoint='http://127.0.0.1:11434/v1/chat/completions' if access == 'local_endpoint' else None,
        metadata={'capacity_source': source, 'terms_status': terms},
    )


class CapacityRouterTests(unittest.TestCase):
    def setUp(self):
        self.task = CompiledTask(
            task_id='task', title='Code', phase='execution', role='executor',
            expected_output_type='contribution', domains=('coding',)
        )

    def test_maximum_free_preserves_challenge_collaboration(self):
        profiles = (
            profile('free-a', 'google', .88, source='official_free', roles=('producer',)),
            profile('free-b', 'other', .90, source='official_free', roles=('challenger',)),
            profile('paid-s', 'premium', .98, roles=('synthesizer',)),
        )
        observations = {
            'free-a': CapacityObservation('free-a', 'official_free', 'available', remaining_tokens=100000),
            'free-b': CapacityObservation('free-b', 'official_free', 'available', remaining_tokens=100000),
            'paid-s': CapacityObservation('paid-s', 'paid', 'available', remaining_tokens=100000, input_cost_per_million=1, output_cost_per_million=4),
        }
        plan = AdaptiveCapacityRouter(observations).plan(
            plan_id='adaptive', display_name='Adaptive', team_mode='challenge', task=self.task,
            profiles=profiles, policy=RoutingPolicy(mode='maximum_free', quality_floor=.7, producer_count=1, fallback_per_role=0)
        )
        self.assertEqual(plan.portfolio.strategy, 'challenge_synthesis')
        self.assertEqual({m.role for m in plan.portfolio.members}, {'producer', 'challenger', 'synthesizer'})
        self.assertIn('free-a', {m.model_id for m in plan.portfolio.members})
        self.assertIn('free-b', {m.model_id for m in plan.portfolio.members})

    def test_exhausted_and_weak_are_not_selected(self):
        profiles = (
            profile('exhausted', 'a', .99, source='official_free'),
            profile('weak', 'b', .30, source='official_free'),
            profile('good', 'c', .85, source='official_free'),
        )
        obs = {
            'exhausted': CapacityObservation('exhausted', 'official_free', 'exhausted'),
            'weak': CapacityObservation('weak', 'official_free', 'available', remaining_tokens=999999),
            'good': CapacityObservation('good', 'official_free', 'available', remaining_tokens=999999),
        }
        plan = AdaptiveCapacityRouter(obs).plan(
            plan_id='solo', display_name='Solo', team_mode='solo', task=self.task, profiles=profiles,
            policy=RoutingPolicy(mode='maximum_free', quality_floor=.7, producer_count=1)
        )
        self.assertEqual(plan.portfolio.members[0].model_id, 'good')
        rejected = {x[0]: x[1] for x in plan.roles[0].rejected}
        self.assertEqual(rejected['exhausted'], 'quota_exhausted')
        self.assertEqual(rejected['weak'], 'quality_floor_not_met')

    def test_unverified_subscription_is_not_api_entitlement(self):
        p = profile('consumer-plan', 'consumer', .99, source='subscription_entitlement')
        router = AdaptiveCapacityRouter({
            'consumer-plan': CapacityObservation('consumer-plan', 'subscription_entitlement', 'available', remaining_tokens=999999, entitlement_verified=False)
        })
        with self.assertRaisesRegex(Exception, 'no_eligible_model_for_role'):
            router.plan(plan_id='x', display_name='x', team_mode='solo', task=self.task, profiles=(p,))

    def test_capacity_store_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            store = CapacityStore(Path(d) / 'capacity.sqlite3')
            item = CapacityObservation('m', 'official_free', 'available', remaining_tokens=1234)
            store.put(item)
            self.assertEqual(store.get('m').remaining_tokens, 1234)
            self.assertEqual(len(store.list()), 1)

    def test_benchmark(self):
        result = run_capacity_router_benchmark()
        self.assertTrue(result['passed'], result)
        self.assertEqual(result['metrics']['provider_network_calls'], 0)
        self.assertEqual(result['metrics']['consumer_subscription_entitlements_assumed'], 0)


if __name__ == '__main__':
    unittest.main()
