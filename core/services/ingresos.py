from decimal import Decimal

from django.utils import timezone

from core.models import EstadoSimulacion, IngresosHogar, Presupuesto, Residente


def cerrar_mes(payload: dict | None = None) -> dict:
    """Cierre de mes (CU nuevo): suma los ingresos de cada residente y renueva el
    presupuesto (reset simple: monto_gastado vuelve a 0 en todas las categorias,
    limite_mensual no cambia).

    El movimiento de plata lo hace IngresosHogar.recibir_ingreso: barre a ahorros
    el saldo que sobro del mes que termina, cancela la deuda pendiente con el
    ingreso nuevo, y lo que queda arranca el saldo del mes nuevo.

    `payload` es el body opcional de /api/ingresos/, generado por el flujo de n8n:
    {"ingresos": [{"telefono": "...", "monto": N}]}
    Un residente que no aparece en el body cae al fallback `ingreso_mensual` cargado
    en su fila de la base (0 por defecto: si n8n no manda nada, no entra plata).
    """
    estado = EstadoSimulacion.actual()
    mes_numero = estado.dia_numero // 30

    ingresos_body = {
        i["telefono"]: Decimal(str(i["monto"]))
        for i in (payload or {}).get("ingresos", [])
        if i.get("telefono") and i.get("monto") is not None
    }

    total = Decimal("0")
    por_residente = {}
    for residente in Residente.objects.all():
        monto_body = ingresos_body.get(residente.telefono)
        monto = monto_body if monto_body is not None else residente.ingreso_mensual

        # El monto real de este cierre lo manda n8n en el body; se persiste aca
        # para que ingreso_mensual deje de quedar siempre en 0 y sirva como
        # fallback real la proxima vez que un residente no aparezca en el body.
        if monto_body is not None and monto_body != residente.ingreso_mensual:
            residente.ingreso_mensual = monto_body
            residente.save(update_fields=["ingreso_mensual"])

        if monto:
            total += monto
            por_residente[residente.nombre] = str(monto)

    categorias = list(Presupuesto.objects.values_list("categoria", flat=True))
    Presupuesto.objects.update(monto_gastado=Decimal("0"), updated_at=timezone.now())

    # Se leen ANTES de recibir_ingreso: el saldo sobrante se barre a ahorros y la
    # deuda se cancela con el ingreso, asi que despues ya no se pueden medir.
    hogar = IngresosHogar.actual()
    ahorrado = hogar.saldo_disponible
    deuda_cancelada = min(hogar.deuda, total)
    hogar.recibir_ingreso(total)

    return {
        "mes_numero": mes_numero,
        "total_ingresos": str(total),
        "por_residente": por_residente,
        "categorias_reseteadas": categorias,
        "ahorrado_del_mes_anterior": str(ahorrado),
        "deuda_cancelada": str(deuda_cancelada),
        "saldo_disponible_hogar": str(hogar.saldo_disponible),
        "ahorros_hogar": str(hogar.ahorros),
        "deuda_hogar": str(hogar.deuda),
    }
