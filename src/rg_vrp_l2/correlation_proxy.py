"""
Simplified correlation risk premium proxy (Driessen, Maenhout & Vilkov, 2009).

When index IV exceeds cap-weighted component IV by a wedge,
prefer index short-vol structures over single-name credits.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CorrelationWedge:
    index_iv: float
    component_iv_weighted: float
    wedge: float
    prefer_index_structures: bool


class CorrelationProxy:
    def __init__(self, wedge_threshold_vol: float = 1.0) -> None:
        self.wedge_threshold = wedge_threshold_vol

    def evaluate(
        self,
        index_iv: float,
        component_ivs: dict[str, float],
        weights: dict[str, float] | None = None,
    ) -> CorrelationWedge:
        if not component_ivs:
            return CorrelationWedge(
                index_iv=index_iv,
                component_iv_weighted=index_iv,
                wedge=0.0,
                prefer_index_structures=False,
            )

        if weights is None:
            w = {k: 1.0 / len(component_ivs) for k in component_ivs}
        else:
            total = sum(weights.get(k, 0) for k in component_ivs)
            w = {k: weights.get(k, 0) / total for k in component_ivs} if total else {}

        comp_iv = sum(component_ivs[k] * w.get(k, 0) for k in component_ivs)
        wedge = index_iv - comp_iv
        prefer = wedge >= self.wedge_threshold

        return CorrelationWedge(
            index_iv=index_iv,
            component_iv_weighted=comp_iv,
            wedge=wedge,
            prefer_index_structures=prefer,
        )
