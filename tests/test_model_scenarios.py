import pytest
from scripts.model_scenarios import SCENARIOS, run_scenario, load_record


@pytest.mark.parametrize('scenario', SCENARIOS)
async def test_recorded_agent_scenarios_are_offline_and_pass(tmp_path, scenario):
    result = await run_scenario(scenario, tmp_path)
    assert result['passed'], result
    assert result['metrics']['real_provider_requests'] == 0
    assert result['metrics']['real_cad_operations'] == 0


def test_replay_rejects_unknown_version_and_scenario(tmp_path):
    path = tmp_path / 'record.json'
    path.write_text('{"version":99,"mode":"offline-synthetic","scenarios":[]}', 'utf-8')
    with pytest.raises(ValueError):
        load_record(path)
    path.write_text('{"version":1,"mode":"offline-synthetic","scenarios":[{"scenario":"arbitrary-code"}]}', 'utf-8')
    with pytest.raises(ValueError):
        load_record(path)
