import tempfile
import unittest
from pathlib import Path

from w1cip.model_access import ModelAccessFabric, ModelAccessStore, ModelPortfolio, ModelProfile, PortfolioMember
from w1cip.orchestrator import CompiledTask, ProviderPermanentError, ProviderRequest, ProviderResponse


class Adapter:
    supports_idempotency = True
    def __init__(self, resource_id, fail=False, marker='ok'):
        self.resource_id=resource_id; self.fail=fail; self.marker=marker; self.calls=[]
    def invoke(self, request):
        self.calls.append(request)
        if self.fail:
            raise ProviderPermanentError('failed')
        return ProviderResponse(output_type='contribution', payload={'marker': self.marker, 'prior': len(request.prior_outputs)})


def p(mid, provider, roles):
    return ModelProfile(
        model_id=mid, display_name=mid, provider_id=provider, connector_type='openai_compatible',
        connector_resource_id=f'r-{mid}', model_name=mid, access_mode='local_endpoint', privacy_mode='local',
        capabilities={'coding': .9, 'general': .9}, roles=roles, domains=('coding',),
        endpoint='http://127.0.0.1:11434/v1/chat/completions'
    )


class RoleFallbackTests(unittest.TestCase):
    def test_adaptive_producer_standby_is_cold_until_primary_failure(self):
        with tempfile.TemporaryDirectory() as d:
            store=ModelAccessStore(Path(d)/'m.sqlite3')
            profiles=[
                p('producer-1','a',('producer',)), p('producer-2','b',('producer',)),
                p('producer-standby','c',('producer',)),
            ]
            for x in profiles: store.put_profile(x)
            store.put_portfolio(ModelPortfolio(
                portfolio_id='adaptive-parallel', display_name='adaptive-parallel', strategy='parallel_collect',
                members=(
                    PortfolioMember('producer-1','producer',100,True),
                    PortfolioMember('producer-2','producer',101,True),
                    PortfolioMember('producer-standby','producer',102,False),
                ),
                max_parallel=2, minimum_successful_producers=1,
                metadata={'adaptive_capacity_router':'1.0','adaptive_primary_counts':{'producer':2}},
            ))
            adapters={
                'producer-1':Adapter('r-p1', fail=True),
                'producer-2':Adapter('r-p2', marker='p2'),
                'producer-standby':Adapter('r-p3', marker='standby'),
            }
            fabric=ModelAccessFabric(store, adapter_overrides=adapters)
            task=CompiledTask(task_id='t',title='t',phase='execution',role='executor',expected_output_type='contribution',domains=('coding',))
            req=ProviderRequest(run_id='r',session_id='s',task=task,resource_id='x',idempotency_key='k',context={},prior_outputs={},attempt=1)
            result=fabric.invoke_portfolio('adaptive-parallel',req)
            self.assertIn('producer_fallback_used',result.warnings)
            self.assertEqual(1,len(adapters['producer-standby'].calls))
            completed={x.model_id for x in result.invocations if x.status=='completed'}
            self.assertEqual({'producer-2','producer-standby'},completed)

    def test_adaptive_producer_standby_not_spent_when_primaries_succeed(self):
        with tempfile.TemporaryDirectory() as d:
            store=ModelAccessStore(Path(d)/'m.sqlite3')
            profiles=[p('producer-1','a',('producer',)),p('producer-2','b',('producer',)),p('producer-standby','c',('producer',))]
            for x in profiles: store.put_profile(x)
            store.put_portfolio(ModelPortfolio(
                portfolio_id='adaptive-parallel', display_name='adaptive-parallel', strategy='parallel_collect',
                members=(PortfolioMember('producer-1','producer',100,True),PortfolioMember('producer-2','producer',101,True),PortfolioMember('producer-standby','producer',102,False)),
                max_parallel=2, minimum_successful_producers=1, metadata={'adaptive_capacity_router':'1.0','adaptive_primary_counts':{'producer':2}},
            ))
            adapters={'producer-1':Adapter('r-p1',marker='p1'),'producer-2':Adapter('r-p2',marker='p2'),'producer-standby':Adapter('r-p3',marker='standby')}
            fabric=ModelAccessFabric(store, adapter_overrides=adapters)
            task=CompiledTask(task_id='t',title='t',phase='execution',role='executor',expected_output_type='contribution',domains=('coding',))
            req=ProviderRequest(run_id='r',session_id='s',task=task,resource_id='x',idempotency_key='k',context={},prior_outputs={},attempt=1)
            result=fabric.invoke_portfolio('adaptive-parallel',req)
            self.assertNotIn('producer_fallback_used',result.warnings)
            self.assertEqual([],adapters['producer-standby'].calls)

    def test_challenger_and_synthesizer_fallback_preserve_handoffs(self):
        with tempfile.TemporaryDirectory() as d:
            store=ModelAccessStore(Path(d)/'m.sqlite3')
            profiles=[
                p('producer','a',('producer',)), p('challenger-1','b',('challenger',)), p('challenger-2','c',('challenger',)),
                p('synth-1','d',('synthesizer',)), p('synth-2','e',('synthesizer',)),
            ]
            for x in profiles: store.put_profile(x)
            store.put_portfolio(ModelPortfolio(
                portfolio_id='challenge', display_name='challenge', strategy='challenge_synthesis',
                members=(
                    PortfolioMember('producer','producer'),
                    PortfolioMember('challenger-1','challenger',100), PortfolioMember('challenger-2','challenger',101),
                    PortfolioMember('synth-1','synthesizer',100), PortfolioMember('synth-2','synthesizer',101),
                )
            ))
            adapters={
                'producer':Adapter('r-producer', marker='producer'),
                'challenger-1':Adapter('r-ch1', fail=True), 'challenger-2':Adapter('r-ch2', marker='challenge'),
                'synth-1':Adapter('r-s1', fail=True), 'synth-2':Adapter('r-s2', marker='final'),
            }
            fabric=ModelAccessFabric(store, adapter_overrides=adapters)
            task=CompiledTask(task_id='t',title='t',phase='execution',role='executor',expected_output_type='contribution',domains=('coding',))
            req=ProviderRequest(run_id='r',session_id='s',task=task,resource_id='x',idempotency_key='k',context={},prior_outputs={},attempt=1)
            result=fabric.invoke_portfolio('challenge',req)
            self.assertEqual(result.selected_model_id,'synth-2')
            self.assertIn('challenger_fallback_used',result.warnings)
            self.assertIn('synthesizer_fallback_used',result.warnings)
            self.assertEqual(adapters['challenger-2'].calls[0].prior_outputs['producer']['payload']['marker'],'producer')
            self.assertIn('_w1_challenge',adapters['synth-2'].calls[0].prior_outputs)


if __name__=='__main__': unittest.main()
