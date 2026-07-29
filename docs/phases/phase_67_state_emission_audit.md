# Fase 67 — auditoría state→emission

La auditoría sobre 117,612 ventanas y 9,801 partidos detectó desalineación
temporal entre el label y la emisión:

- implementación actual: `state_t → goles_t`;
- relación causal candidata: `state_t → goles_t+1`.

Brechas de goles por estado entre misma y siguiente ventana:

- `equilibrio`: `-0.023509`;
- `presion`: `-0.054021`;
- `repliegue`: `-0.044080`;
- `desorganizacion`: `-0.032509`.

La clasificación queda `state_emission_temporal_misalignment_detected`.
Además, las reglas actuales usan presión, faltas y tarjetas, pero no incorporan
disparos, tiros a puerta, corners ni goles como evidencia semántica principal.

Artefacto: `artifacts/phase_67_state_emission_audit_v1/`.

