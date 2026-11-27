"""Smart parent authorization with unchanged bounded R31 ADC/receipt implementation."""
from adapters.arclight import ArcLightAdapter
from adapters.arclight_rabi16_adapter import ArcLightRabi16Adapter
from adapters.f8 import AdapterError
from adapters.arclight_smart_rabi16_runtime import policy


class ArcLightSmartRabi16Adapter(ArcLightRabi16Adapter):
    def __init__(self, project_root, profile):
        try: policy.verify_profile(project_root,profile)
        except (OSError,ValueError,KeyError,TypeError) as exc:
            raise AdapterError('SMART_RABI_SAMPLING_PROFILE_INVALID',str(exc)) from None
        ArcLightAdapter.__init__(self,project_root,profile)

    def capabilities(self):
        result = super().capabilities()
        result['executionPolicy'] = policy.EXECUTION_POLICY
        return result

    def build_plan(self, request, output_dir=None):
        try: binding = policy.validate_binding(self.root,self.profile,request)
        except (OSError,ValueError,KeyError,TypeError) as exc:
            raise AdapterError('SMART_RABI_SAMPLING_AUTHORIZATION_INVALID',str(exc)) from None
        plan = ArcLightAdapter.build_plan(self,request,output_dir)
        count = plan['parameters']['acquisitions_per_point']
        if count != 16 or plan['parameters']['shots'] != 2000:
            raise AdapterError('SMART_RABI_SAMPLING_COUNTS','智能16块版本固定每点16块、每块2000 shots')
        artifacts = plan['totalRows']*(count+1)+policy.COMMON_ARTIFACT_RESERVE
        if artifacts > policy.MAX_REGISTERED_ARTIFACTS:
            raise AdapterError('SAMPLING_ARTIFACT_BUDGET','独立分块证据超出存储上限，请减少扫描点或轮次')
        plan.update(executionBinding=binding,samplingPolicy=policy.POLICY,
                    executionPolicy=policy.EXECUTION_POLICY,maximumRegisteredArtifacts=artifacts)
        return plan

    def _result(self,directory,plan,request,completion):
        result = super()._result(directory,plan,request,completion)
        result['provenance']['executionPolicy'] = policy.EXECUTION_POLICY
        return result
