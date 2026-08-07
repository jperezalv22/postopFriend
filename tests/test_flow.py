"""La máquina de estados. Función pura: sin API, sin red, sin sorpresas."""

from app.agent.flow import (
    MAX_REINTENTOS_POR_VARIABLE,
    Contexto,
    Estado,
    Intencion,
    objetivo_del_turno,
    puede_cerrar,
    siguiente_variable,
    transicion,
)
from app.triage.models import Decision, EstadoClinico, Nivel, Variable


def var(valor, evidencia="lo dijo"):
    return Variable(valor=valor, evidencia=evidencia, confianza=0.9)


def decision(nivel: Nivel, score: int = 0, red_flags=None) -> Decision:
    return Decision(nivel=nivel, score=score, red_flags=red_flags or [])


class TestRecorridoNormal:
    def test_de_apertura_a_protocolo(self):
        ctx = Contexto()
        assert transicion(ctx, Intencion.RESPONDE) is Estado.PROTOCOLO
        assert ctx.identidad_confirmada

    def test_el_protocolo_se_queda_hasta_resolver_las_variables(self):
        ctx = Contexto(estado=Estado.PROTOCOLO)
        ctx.estado_clinico.dolor_nrs = var(3)
        assert transicion(ctx, Intencion.RESPONDE) is Estado.PROTOCOLO

    def test_pasa_a_evaluacion_cuando_no_queda_nada_por_preguntar(self):
        ctx = Contexto(estado=Estado.PROTOCOLO)
        e = ctx.estado_clinico
        e.dolor_nrs, e.fiebre_c, e.herida = var(2), var(36.8), var("normal")
        e.movilidad, e.apetito, e.sueno = var("normal"), var("normal"), var("normal")
        assert transicion(ctx, Intencion.RESPONDE) is Estado.EVALUACION

    def test_verde_cierra_sin_escalar(self):
        ctx = Contexto(estado=Estado.EVALUACION)
        assert transicion(ctx, Intencion.RESPONDE, decision(Nivel.VERDE)) is Estado.CIERRE

    def test_amarillo_escala_antes_de_cerrar(self):
        ctx = Contexto(estado=Estado.EVALUACION)
        assert transicion(ctx, Intencion.RESPONDE, decision(Nivel.AMARILLO, 3)) is Estado.ESCALAR
        assert transicion(ctx, Intencion.RESPONDE) is Estado.CIERRE

    def test_el_cierre_siempre_termina_en_acta(self):
        ctx = Contexto(estado=Estado.CIERRE)
        assert transicion(ctx, Intencion.DESPEDIDA) is Estado.ACTA


class TestEmergencia:
    def test_corta_el_protocolo_desde_cualquier_estado(self):
        for origen in (Estado.APERTURA, Estado.PROTOCOLO, Estado.INDAGACION,
                       Estado.RESPUESTA_CLINICA, Estado.EVALUACION):
            ctx = Contexto(estado=origen)
            assert transicion(ctx, Intencion.EMERGENCIA) is Estado.EMERGENCIA
            assert "emergencia_detectada" in ctx.incidencias

    def test_una_bandera_roja_del_motor_tambien_la_dispara(self):
        ctx = Contexto(estado=Estado.PROTOCOLO)
        d = decision(Nivel.ROJO, 3, ["secrecion_purulenta"])
        assert transicion(ctx, Intencion.RESPONDE, d) is Estado.EMERGENCIA

    def test_desde_emergencia_se_escala_siempre(self):
        ctx = Contexto(estado=Estado.EMERGENCIA)
        assert transicion(ctx, Intencion.RESPONDE) is Estado.ESCALAR


class TestIndagacion:
    def test_una_evasiva_repregunta_la_misma_variable(self):
        ctx = Contexto(estado=Estado.PROTOCOLO, variable_en_curso="fiebre_c")
        assert transicion(ctx, Intencion.EVASIVA) is Estado.INDAGACION
        assert ctx.reintentos["fiebre_c"] == 1
        assert ctx.variable_en_curso == "fiebre_c", "no se pasa a la siguiente pregunta"

    def test_tras_dos_reintentos_la_variable_se_deja_ir(self):
        ctx = Contexto(estado=Estado.PROTOCOLO, variable_en_curso="fiebre_c")
        for _ in range(MAX_REINTENTOS_POR_VARIABLE):
            transicion(ctx, Intencion.EVASIVA)
        # Insistir una tercera vez hace que el paciente cuelgue.
        assert siguiente_variable(ctx.estado_clinico, ctx.reintentos) != "fiebre_c"

    def test_una_pregunta_del_paciente_desvia_y_vuelve(self):
        ctx = Contexto(estado=Estado.PROTOCOLO)
        assert transicion(ctx, Intencion.PREGUNTA_CLINICA) is Estado.RESPUESTA_CLINICA
        assert transicion(ctx, Intencion.RESPONDE) is Estado.PROTOCOLO

    def test_un_tema_ajeno_se_reencauza(self):
        ctx = Contexto(estado=Estado.PROTOCOLO)
        assert transicion(ctx, Intencion.FUERA_DE_MISION) is Estado.FUERA_DE_GUION
        assert transicion(ctx, Intencion.RESPONDE) is Estado.PROTOCOLO

    def test_una_inyeccion_se_reencauza_y_queda_registrada(self):
        ctx = Contexto(estado=Estado.PROTOCOLO)
        assert transicion(ctx, Intencion.INYECCION) is Estado.FUERA_DE_GUION
        assert "intento_de_inyeccion" in ctx.incidencias


class TestNoSePuedeCerrarSinDecidir:
    """La regla que responde al sub-criterio de ambigüedad de la rúbrica."""

    def test_no_cierra_con_una_critica_pendiente_y_reintentos_disponibles(self):
        ctx = Contexto(estado=Estado.EVALUACION)
        ctx.decision = decision(Nivel.INDETERMINADO)
        assert not puede_cerrar(ctx)

    def test_indeterminado_devuelve_a_indagacion(self):
        ctx = Contexto(estado=Estado.EVALUACION)
        assert transicion(ctx, Intencion.RESPONDE, decision(Nivel.INDETERMINADO)) is Estado.INDAGACION

    def test_cierra_cuando_se_agotan_los_reintentos(self):
        ctx = Contexto(estado=Estado.EVALUACION)
        ctx.estado_clinico = EstadoClinico(dolor_nrs=var(2))
        ctx.reintentos = {"fiebre_c": 2, "herida": 2}
        ctx.decision = decision(Nivel.INDETERMINADO)
        assert ctx.intentos_agotados
        assert puede_cerrar(ctx)

    def test_un_paciente_que_no_puede_hablar_si_cierra(self):
        ctx = Contexto(estado=Estado.PROTOCOLO)
        assert transicion(ctx, Intencion.NO_DISPONIBLE) is Estado.CIERRE_NO_DISPONIBLE
        assert puede_cerrar(ctx)
        assert "paciente_no_disponible" in ctx.incidencias


class TestObjetivoDelTurno:
    def test_dice_que_variable_toca_y_con_que_pregunta(self):
        ctx = Contexto(estado=Estado.PROTOCOLO)
        objetivo = objetivo_del_turno(ctx)
        assert objetivo["variable_objetivo"] == "dolor_nrs"
        assert "cero a diez" in objetivo["pregunta_sugerida"]

    def test_el_orden_sigue_el_valor_clinico(self):
        ctx = Contexto()
        vistos = []
        for _ in range(6):
            variable = siguiente_variable(ctx.estado_clinico, ctx.reintentos)
            vistos.append(variable)
            setattr(ctx.estado_clinico, variable, var("normal"))
        assert vistos == ["dolor_nrs", "fiebre_c", "herida", "movilidad", "apetito", "sueno"]

    def test_marca_cuando_es_un_reintento(self):
        ctx = Contexto(estado=Estado.INDAGACION, variable_en_curso="herida")
        ctx.reintentos["herida"] = 1
        assert objetivo_del_turno(ctx)["es_reintento"]

    def test_la_pregunta_sugerida_lleva_un_hecho_comprobable(self):
        # Contra el paciente minimizador, «¿cómo va la herida?» se contesta «bien».
        ctx = Contexto(estado=Estado.PROTOCOLO, variable_en_curso="herida")
        assert "líquido" in objetivo_del_turno(ctx)["pregunta_sugerida"]
