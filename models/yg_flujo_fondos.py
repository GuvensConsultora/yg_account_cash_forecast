# Yagüven C.G. — Licencia LGPL-3.
"""Flujo de fondos proyectado: informe «a medida» del visor de yg_account_reports.

Todo se ubica por FECHA DE VENCIMIENTO:
* comprobantes abiertos (cuentas a cobrar / a pagar sin conciliar): date_maturity de cada cuota;
* compromisos futuros cargados a mano: su fecha de vencimiento (y las repeticiones).
Lo vencido y no cancelado se suma a la primera columna y se avisa.
"""
from collections import defaultdict
from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models

AGRUPACIONES = [("dia", "Por día"), ("semana", "Por semana"), ("mes", "Por mes")]
SALDOS = [("extracto", "Saldo inicial según extracto bancario"), ("contable", "Saldo inicial según contabilidad")]


def _fmt(valor):
    texto = "{:,.2f}".format(abs(valor)).replace(",", "X").replace(".", ",").replace("X", ".")
    return "(%s)" % texto if valor < 0 else texto


class YgFlujoFondos(models.AbstractModel):
    _name = "yg.flujo.fondos"
    _description = "Flujo de fondos proyectado (informe a medida)"

    # ------------------------------------------------------------------ opciones
    @api.model
    def _yg_opciones_extra(self, informe, previas):
        hoy = fields.Date.context_today(self)
        agrupar = previas.get("agrupar") or "semana"
        horizonte = hoy + {"dia": relativedelta(days=30), "semana": relativedelta(weeks=13, days=-1),
                           "mes": relativedelta(months=12, days=-1)}.get(agrupar, relativedelta(weeks=13, days=-1))
        return {"desde": fields.Date.to_string(hoy), "hasta": fields.Date.to_string(horizonte),
                "agrupar": "semana", "saldo_inicial": "extracto"}

    @api.model
    def _yg_filtros_extra(self, informe, opciones):
        return [
            {"clave": "agrupar", "etiqueta": "Agrupar", "icono": "▤", "valores": AGRUPACIONES,
             "reinicia": ["desde", "hasta"]},  # al cambiar, vuelve al horizonte por defecto (31 días / 13 semanas / 12 meses)
            {"clave": "saldo_inicial", "etiqueta": "Saldo inicial", "icono": "🏦", "valores": SALDOS},
        ]

    # ------------------------------------------------------------------ períodos
    @api.model
    def _yg_tramos(self, opciones):
        """[(inicio, fin, nombre)]. Semanas de lunes a domingo; meses calendario."""
        desde, hasta = fields.Date.to_date(opciones["desde"]), fields.Date.to_date(opciones["hasta"])
        tramos = []
        inicio = desde
        while inicio <= hasta:
            if opciones.get("agrupar") == "dia":
                fin = inicio
                nombre = inicio.strftime("%d/%m")
            elif opciones.get("agrupar") == "mes":
                fin = (inicio + relativedelta(day=1, months=1)) - timedelta(days=1)
                nombre = inicio.strftime("%m/%Y")
            else:
                fin = inicio + timedelta(days=6 - inicio.weekday())
                nombre = "Sem. %s" % inicio.strftime("%d/%m")
            fin = min(fin, hasta)
            tramos.append((inicio, fin, nombre))
            inicio = fin + timedelta(days=1)
        return tramos

    @api.model
    def _yg_columnas(self, informe, opciones):
        periodo = opciones["periodos"][0]["nombre"]
        cols = [{"periodo": periodo, "nombre": t[2], "etiqueta": "t%d" % i, "tipo": "monetary"}
                for i, t in enumerate(self._yg_tramos(opciones))]
        cols.append({"periodo": periodo, "nombre": "Total", "etiqueta": "total", "tipo": "monetary"})
        return cols

    # ------------------------------------------------------------------ datos
    @api.model
    def _yg_diarios_caja(self):
        return self.env["account.journal"].search([("type", "in", ("bank", "cash")),
                                                   ("company_id", "in", self.env.companies.ids)])

    @api.model
    def _yg_saldo_inicial(self, opciones):
        """(importe, detalle por diario) a la fecha «desde»."""
        desde = fields.Date.to_date(opciones["desde"])
        detalle = []
        for diario in self._yg_diarios_caja():
            importe, fuente = None, "contabilidad"
            if opciones.get("saldo_inicial") == "extracto":
                ultima = self.env["account.bank.statement.line"].search(
                    [("journal_id", "=", diario.id), ("date", "<=", desde)], order="date desc, sequence desc, id desc", limit=1)
                if ultima:
                    importe, fuente = ultima.running_balance, "extracto al %s" % ultima.date.strftime("%d/%m/%Y")
            if importe is None and diario.default_account_id:
                grupos = self.env["account.move.line"]._read_group(
                    [("account_id", "=", diario.default_account_id.id), ("parent_state", "=", "posted"),
                     ("date", "<=", desde)], [], ["balance:sum"])
                importe = grupos[0][0] or 0.0
            detalle.append((diario, importe or 0.0, fuente))
        return sum(d[1] for d in detalle), detalle

    @api.model
    def _yg_comprobantes(self, opciones):
        hasta = fields.Date.to_date(opciones["hasta"])
        estados = ("draft", "posted") if opciones.get("borradores") else ("posted",)
        lineas = self.env["account.move.line"].search([
            ("company_id", "in", self.env.companies.ids),
            ("account_id.account_type", "in", ("asset_receivable", "liability_payable")),
            ("parent_state", "in", estados), ("reconciled", "=", False), ("amount_residual", "!=", 0),
        ])
        return lineas.filtered(lambda l: (l.date_maturity or l.date) <= hasta)

    @api.model
    def _yg_tramo_de(self, fecha, tramos):
        if fecha < tramos[0][0]:
            return 0  # vencido: va a la primera columna
        for i, (ini, fin, _n) in enumerate(tramos):
            if ini <= fecha <= fin:
                return i
        return None

    # ------------------------------------------------------------------ líneas
    @api.model
    def _yg_lineas(self, informe, opciones):
        tramos = self._yg_tramos(opciones)
        n = len(tramos)
        desde, hasta = fields.Date.to_date(opciones["desde"]), fields.Date.to_date(opciones["hasta"])
        desplegadas = set(opciones.get("desplegadas") or [])
        todo = opciones.get("desplegar_todo")

        # Grupos: clave de grupo -> {clave de detalle -> {"nombre", "valores", "auditar"}}
        grupos = {g: {} for g in ("cobros", "comp_ing", "pagos", "comp_egr")}

        def sumar(grupo, clave, nombre, i, importe):
            item = grupos[grupo].setdefault(clave, {"nombre": nombre, "valores": [0.0] * n})
            item["valores"][i] += importe

        for l in self._yg_comprobantes(opciones):
            i = self._yg_tramo_de(l.date_maturity or l.date, tramos)
            if i is None:
                continue
            grupo = "cobros" if l.account_id.account_type == "asset_receivable" else "pagos"
            nombre = "%s · %s" % (l.move_id.name, l.partner_id.name or "sin contacto")
            sumar(grupo, ("move", l.move_id.id), nombre, i, l.amount_residual)

        compromisos = self.env["yg.flujo.compromiso"].search([("company_id", "in", self.env.companies.ids)])
        for c, fecha, importe in compromisos.yg_vencimientos(desde, hasta):
            i = self._yg_tramo_de(fecha, tramos)
            if i is None:
                continue
            grupo = "comp_ing" if importe > 0 else "comp_egr"
            nombre = "%s%s" % ("%s · " % c.categoria if c.categoria else "", c.name)
            sumar(grupo, ("comp", c.id), nombre, i, importe)

        def total_grupo(g):
            return [sum(item["valores"][i] for item in grupos[g].values()) for i in range(n)]

        def con_total(valores):
            return valores + [sum(valores)]

        def fila(id_, nombre, valores, nivel, tipo="linea", desplegable=False, auditable=False, **extra):
            d = {"id": id_, "nombre": nombre, "codigo": "", "nivel": nivel, "valores": valores,
                 "desplegable": desplegable, "desplegada": desplegable and (todo or id_ in desplegadas),
                 "auditable": auditable, "tipo": tipo}
            d.update(extra)
            return d

        saldo_ini, _det = self._yg_saldo_inicial(opciones)
        cobros, comp_ing = total_grupo("cobros"), total_grupo("comp_ing")
        pagos, comp_egr = total_grupo("pagos"), total_grupo("comp_egr")
        ingresos = [cobros[i] + comp_ing[i] for i in range(n)]
        egresos = [pagos[i] + comp_egr[i] for i in range(n)]
        neto = [ingresos[i] + egresos[i] for i in range(n)]
        iniciales, finales, saldo = [], [], saldo_ini
        for i in range(n):
            iniciales.append(saldo)
            saldo += neto[i]
            finales.append(saldo)
        a_cubrir, peor = [], 0.0
        for v in finales:
            peor = min(peor, v)
            a_cubrir.append(-peor)

        lineas = [fila("saldo_ini", "Saldo inicial de caja y bancos", iniciales + [saldo_ini], 0, "saldo",
                       marcar_negativos=True)]

        def bloque(id_, nombre, grupo, valores):
            fl = fila(id_, nombre, con_total(valores), 1, desplegable=bool(grupos[grupo]), auditable=True)
            lineas.append(fl)
            if fl["desplegada"]:
                for clave, item in sorted(grupos[grupo].items(), key=lambda kv: kv[1]["nombre"]):
                    lineas.append(fila("%s~%s~%s" % (id_, clave[0], clave[1]), item["nombre"],
                                       con_total(item["valores"]), 2, "detalle", auditable=True))

        lineas.append(fila("ingresos", "INGRESOS", con_total(ingresos), 0, "total"))
        bloque("cobros", "Cobranzas de facturas", "cobros", cobros)
        bloque("comp_ing", "Compromisos a cobrar", "comp_ing", comp_ing)
        lineas.append(fila("egresos", "EGRESOS", con_total(egresos), 0, "total"))
        bloque("pagos", "Pagos de facturas, cuotas y planes", "pagos", pagos)
        bloque("comp_egr", "Compromisos a pagar", "comp_egr", comp_egr)
        lineas.append(fila("neto", "Flujo neto del período", con_total(neto), 0, "total"))
        lineas.append(fila("saldo_fin", "Saldo final proyectado", finales + [finales[-1] if finales else saldo_ini],
                           0, "saldo", marcar_negativos=True))
        lineas.append(fila("a_cubrir", "A cubrir (aporte necesario acumulado)", a_cubrir + [max(a_cubrir or [0.0])],
                           0, "alerta"))
        return lineas

    @api.model
    def _yg_avisos(self, informe, opciones, datos):
        avisos = []
        saldo_ini, detalle = self._yg_saldo_inicial(opciones)
        partes = ["%s %s (%s)" % (d.code, _fmt(v), f) for d, v, f in detalle if v or f != "contabilidad"]
        avisos.append({"tipo": "info", "texto": "Saldo inicial %s: %s." % (
            _fmt(saldo_ini), "; ".join(partes) or "sin movimientos")})
        desde = fields.Date.to_date(opciones["desde"])
        vencidas = self._yg_comprobantes(opciones).filtered(lambda l: (l.date_maturity or l.date) < desde)
        if vencidas:
            avisos.append({"tipo": "alerta", "texto": "%d vencimiento(s) ya pasados y sin cancelar por %s: van en la primera columna." % (
                len(vencidas), _fmt(sum(vencidas.mapped("amount_residual"))))})
        fila_saldo = next(l for l in datos["lineas"] if l["id"] == "saldo_fin")
        nombres = [c["nombre"] for c in datos["columnas"]]
        negativos = [i for i, v in enumerate(fila_saldo["valores"][:-1]) if v < -0.005]
        if negativos:
            peor = min(range(len(fila_saldo["valores"]) - 1), key=lambda i: fila_saldo["valores"][i])
            avisos.append({"tipo": "alerta", "texto": "El saldo queda en negativo desde %s. Hace falta cubrir hasta %s (el punto más bajo es en %s)." % (
                nombres[negativos[0]], _fmt(-fila_saldo["valores"][peor]), nombres[peor])})
        else:
            avisos.append({"tipo": "info", "texto": "El saldo proyectado no queda en negativo en el horizonte elegido."})
        return avisos

    # ------------------------------------------------------------------ auditoría
    @api.model
    def _yg_auditar(self, informe, opciones, id_linea, columna):
        partes = id_linea.split("~")
        if len(partes) == 3 and partes[1] == "move":
            return {"type": "ir.actions.act_window", "res_model": "account.move", "res_id": int(partes[2]),
                    "views": [[False, "form"]], "target": "current"}
        if len(partes) == 3 and partes[1] == "comp":
            return {"type": "ir.actions.act_window", "res_model": "yg.flujo.compromiso", "res_id": int(partes[2]),
                    "views": [[False, "form"]], "target": "current"}
        if id_linea in ("comp_ing", "comp_egr"):
            return {"type": "ir.actions.act_window", "name": "Compromisos futuros", "res_model": "yg.flujo.compromiso",
                    "views": [[False, "list"], [False, "form"]],
                    "domain": [("tipo", "=", "ingreso" if id_linea == "comp_ing" else "egreso"), ("cumplido", "=", False)]}
        tipo = "asset_receivable" if id_linea == "cobros" else "liability_payable"
        ids = self._yg_comprobantes(opciones).filtered(lambda l: l.account_id.account_type == tipo).ids
        return {"type": "ir.actions.act_window", "name": "Vencimientos abiertos", "res_model": "account.move.line",
                "views": [[False, "list"], [False, "form"]], "domain": [("id", "in", ids)],
                "context": {"create": False}}
