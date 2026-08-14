from decimal import Decimal

from core.models import Presupuesto

"""Validador transversal de gasto (CU-01, CU-03).

Es puramente deterministico: no llama al LLM. La aprobacion de un gasto es una
regla de negocio verificable (saldo disponible), no algo que deba "razonar" un modelo.
"""


def aprobar_gasto(categoria: str | None, monto: Decimal) -> tuple[bool, Presupuesto | None]:
    if not categoria:
        return True, None

    try:
        presupuesto = Presupuesto.objects.get(categoria=categoria)
    except Presupuesto.DoesNotExist:
        return True, None

    saldo_disponible = presupuesto.limite_mensual - presupuesto.monto_gastado
    return monto <= saldo_disponible, presupuesto


def tiene_saldo_disponible(categoria: str) -> tuple[bool, Presupuesto | None]:
    """Para gastos sin monto exacto todavia (ej. agendar un service): alcanza con
    que la categoria tenga saldo mayor a cero para no bloquear la accion.
    """
    try:
        presupuesto = Presupuesto.objects.get(categoria=categoria)
    except Presupuesto.DoesNotExist:
        return True, None

    saldo_disponible = presupuesto.limite_mensual - presupuesto.monto_gastado
    return saldo_disponible > 0, presupuesto
