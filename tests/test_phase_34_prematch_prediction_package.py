"""Pruebas del contrato de inferencia pre-match prospectiva."""

from src.phase_34_prematch_prediction_package import ALL_TARGETS, _route


def test_route_uses_markov_only_for_selected_targets() -> None:
    """El router conserva baseline en targets no seleccionados."""

    row = {f"prob_{name}": 0.5 for name in ALL_TARGETS}; row["prob_first_half_goal"], row["prob_second_half_goal"] = 0.7, 0.8
    selected = {name: "baseline_temporal_prevalence" for name in ALL_TARGETS}; selected["first_half_goal"] = "markov_dependent_v2"
    baseline = {name: 0.4 for name in ALL_TARGETS}; baseline["first_half_goal"], baseline["second_half_goal"] = 0.6, 0.75
    output = _route(row, selected, baseline)
    assert output["routed_probability_first_half_goal"] == 0.7
    assert output["routed_probability_second_half_goal"] == 0.75


def test_route_does_not_create_target_or_loss_fields() -> None:
    """La ruta pre-match sólo publica probabilidades y provenance."""

    row = {f"prob_{name}": 0.5 for name in ALL_TARGETS}
    selected = {name: "baseline_temporal_prevalence" for name in ALL_TARGETS}
    output = _route(row, selected, {name: 0.4 for name in ALL_TARGETS})
    assert not any(key.startswith("target_") or key.startswith("loss_") for key in output)

# Version: 1.0.0
# Created: 2026-07-26
