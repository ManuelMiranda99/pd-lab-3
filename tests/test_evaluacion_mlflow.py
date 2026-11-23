"""Observador de MLflow de la evaluación (`eval/correr.py --mlflow`, D44), sin LLM ni servidor:
corrida sintética con un ejecutor falso y un almacén sqlite temporal."""

from __future__ import annotations

from datetime import datetime

import pytest

from novapedidos.api.interpretes import INTERPRETACION_ANEXO, MENSAJES_ANEXO
from novapedidos.evaluacion.corrida import correr_evaluacion, guardar_resultados
from novapedidos.evaluacion.ejecutores import Ejecucion
from novapedidos.evaluacion.mlflow_obs import (
    SCORERS,
    ObservadorMlflow,
    filas_casos,
    metricas_de_resumen,
)
from novapedidos.motor import armar_pedido
from test_api import construir_datos_prueba

mlflow = pytest.importorskip("mlflow")


@pytest.fixture(scope="module")
def datos():
    return construir_datos_prueba()


@pytest.fixture
def corrida(datos, tmp_path):
    esperado = armar_pedido(
        "C001", datetime(2026, 11, 23, 15, 20), INTERPRETACION_ANEXO, datos
    ).model_dump(mode="json")
    entrada = {
        "cliente_id": "C001",
        "fecha_hora": "2026-11-23T15:20",
        "mensajes": list(MENSAJES_ANEXO),
    }
    casos = [
        {
            "id": "T1",
            "tipo": "simple",
            "_origen": "a.jsonl",
            "entrada": entrada,
            "esperado": esperado,
        },
        {
            "id": "T2",
            "tipo": "ambiguo",
            "_origen": "b.jsonl",
            "entrada": entrada,
            "esperado": esperado,
        },
    ]

    def ejecutor(caso):
        if caso["id"] == "T2":
            return Ejecucion(latencia_s=0.2, error="ErrorInterprete: caído")
        meta = {"latencia_s": 0.09, "costo_usd": 0.0002, "tokens_entrada": 100}
        return Ejecucion(obtenido=esperado, latencia_s=0.1, codigo_http=200, meta_llm=meta)

    resultados, resumen = correr_evaluacion(casos, ejecutor, datos, concurrencia=2)
    resumen |= {"marca": "prueba", "archivos": ["a.jsonl", "b.jsonl"], "modelo": "falso/m"}
    directorio = tmp_path / "res"
    guardar_resultados(directorio, resultados, resumen)
    return resultados, resumen, directorio


def test_metricas_y_filas_de_la_corrida(corrida):
    resultados, resumen, _ = corrida
    m = metricas_de_resumen(resumen)
    assert m["exactitud"] == 0.5
    assert m["exactitud.conjunto.a"] == 1.0
    assert m["exactitud.conjunto.b"] == 0.0
    assert m["exactitud.tipo.ambiguo"] == 0.0
    assert m["calificador.integridad_precios"] == 0.5
    assert m["costo_total_usd"] == pytest.approx(0.0002)
    assert {"latencia_p50_s", "latencia_p95_s", "latencia_max_s"} <= set(m)
    filas = filas_casos(resultados)
    assert [f["exacto"] for f in filas] == [True, False]
    assert filas[1]["error"].startswith("ErrorInterprete")


def test_observador_registra_run_con_metricas_artefactos_y_evaluacion(corrida, datos, tmp_path):
    resultados, resumen, directorio = corrida
    uri = f"sqlite:///{tmp_path / 'mlflow.db'}"
    obs = ObservadorMlflow(uri, "eval-prueba", datos, parametros={"variante": "base"})
    obs.verificar()
    obs.al_final(resumen, resultados, directorio)
    assert obs.error_genai is None
    cliente = mlflow.MlflowClient(uri)
    run = cliente.get_run(obs.run_id)
    assert run.data.params["variante"] == "base"
    assert run.data.params["modelo"] == "falso/m"
    assert run.data.metrics["exactitud"] == 0.5
    assert run.data.metrics["caso_exacto/mean"] == 0.5  # de mlflow.genai.evaluate
    artefactos = {a.path for a in cliente.list_artifacts(obs.run_id)}
    assert {"resultados.jsonl", "resumen.json", "reporte.txt", "casos.json", "fallos.json"} <= (
        artefactos
    )
    trazas = cliente.search_traces(locations=[obs.experiment_id], max_results=10)
    assert len(trazas) == 2
    nombres = {a.name for t in trazas for a in t.info.assessments}
    assert set(SCORERS) <= nombres
    mlflow.set_tracking_uri(None)
