"""Temporal orchestration adapter (design/details/orchestration-provider-seam.md).

A single, data-driven Temporal workflow interprets a StageGraph — stages become activities that
run governed SwarmKit stage runs, gate resolutions and external events become signals, and
compensation is the saga pattern. The graph stays *data* (topology-as-data at the sequencing
layer); one workflow runs any pipeline.
"""

from __future__ import annotations

from ._adapter import TemporalOrchestrator, pipeline_worker
from ._workflow import PipelineWorkflow

__all__ = ["PipelineWorkflow", "TemporalOrchestrator", "pipeline_worker"]
