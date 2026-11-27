"""Amplitude-only sampling extension; preserves the original native waveform/fit code."""
from copy import deepcopy
from pathlib import Path
from .arclight import ArcLightAdapter
from .f8 import AdapterError
from .arclight_rabi16_runtime import policy


class ArcLightRabi16Adapter(ArcLightAdapter):
    def __init__(self, project_root, profile):
        try:
            policy.verify_profile(project_root, profile)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise AdapterError('RABI_SAMPLING_PROFILE_INVALID', str(exc)) from None
        super().__init__(project_root, profile)

    def capabilities(self):
        result = super().capabilities()
        result['procedures'] = {'sq_rabi_amplitude': result['procedures']['sq_rabi_amplitude']}
        result['samplingPolicy'] = policy.POLICY
        result['requiresAttemptAuthorization'] = True
        return result

    def parameter_schema(self, procedure_id):
        return super().parameter_schema(procedure_id) if procedure_id == 'sq_rabi_amplitude' else {}

    def build_plan(self, request, output_dir=None):
        if request.get('procedureId') != 'sq_rabi_amplitude':
            raise AdapterError('UNSUPPORTED_PROCEDURE', '16 块扩展仅支持幅度 Rabi')
        try:
            binding = policy.validate_binding(self.root, self.profile, request)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise AdapterError('RABI_SAMPLING_AUTHORIZATION_INVALID', str(exc)) from None
        plan = super().build_plan(request, output_dir)
        # Every actual block plus its per-row manifest remains registered. Reject
        # excessive metadata before any operation is reserved or instrument opens.
        artifacts = plan['totalRows'] * (plan['parameters']['acquisitions_per_point'] + 1) + policy.COMMON_ARTIFACT_RESERVE
        if artifacts > policy.MAX_REGISTERED_ARTIFACTS:
            raise AdapterError('SAMPLING_ARTIFACT_BUDGET', '独立分块证据数量超过本执行版本上限，请减少扫描点或轮次')
        plan['executionBinding'] = binding
        plan['samplingPolicy'] = policy.POLICY
        plan['maximumRegisteredArtifacts'] = artifacts
        return plan

    def _result(self, directory, plan, request, completion):
        from .arclight_rabi16_runtime.execute_worker import validate_sampling_receipt
        validate_sampling_receipt(directory, plan, completion)
        result = super()._result(directory, plan, request, completion)
        result['provenance'].update(executionBinding=deepcopy(plan['executionBinding']),
                                    samplingPolicy=policy.POLICY,
                                    samplingReceipt=deepcopy(completion.get('samplingReceipt')))
        return result

    def _artifacts(self, directory, files):
        """Preserve each operation/path even when frozen metadata bytes repeat."""
        directory = Path(directory).resolve()
        if not directory.is_relative_to(self.operations.resolve()):
            raise AdapterError('RESULT_PROVENANCE_INVALID', 'Operation is outside this adapter archive')
        artifacts = super()._artifacts(directory, files)
        operation = directory.relative_to(self.operations.resolve()).as_posix()
        for artifact in artifacts:
            relative = Path(artifact['sourcePath']).resolve().relative_to(directory).as_posix()
            identity = 'real-rabi16-' + policy.digest({'operation': operation, 'path': relative,
                                                      'sha256': artifact['sha256']})[:32]
            artifact.update(id=identity, url='/api/artifacts/' + identity)
        return artifacts
