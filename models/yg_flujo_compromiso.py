# Yagüven C.G. — Licencia LGPL-3.
from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.exceptions import ValidationError


class YgFlujoCompromiso(models.Model):
    _name = "yg.flujo.compromiso"
    _description = "Compromiso futuro del flujo de fondos"
    _inherit = ["mail.thread"]
    _order = "fecha_vencimiento, id"

    name = fields.Char(string="Concepto", required=True, tracking=True)
    tipo = fields.Selection([("egreso", "Egreso (sale plata)"), ("ingreso", "Ingreso (entra plata)")],
                            string="Tipo", required=True, default="egreso", tracking=True)
    fecha_vencimiento = fields.Date(string="Fecha de vencimiento", required=True, tracking=True,
                                    help="Primera (o única) fecha en que hay que pagar o se espera cobrar.")
    importe = fields.Monetary(string="Importe", required=True, tracking=True,
                              help="Siempre en positivo; el tipo indica si entra o sale.")
    recurrencia = fields.Selection([("unica", "Única vez"), ("semanal", "Semanal"), ("mensual", "Mensual")],
                                   string="Se repite", required=True, default="unica", tracking=True)
    hasta = fields.Date(string="Repetir hasta", tracking=True,
                        help="Último vencimiento de la serie. Vacío = hasta el final del horizonte del informe.")
    partner_id = fields.Many2one("res.partner", string="Contacto")
    categoria = fields.Char(string="Categoría", help="Para agrupar en el informe: Sueldos, Alquiler, Servicios…")
    cumplido = fields.Boolean(string="Cumplido", tracking=True,
                              help="Marcarlo cuando ya está registrado en Odoo (factura o pago), para que no cuente dos veces.")
    cumplido_hasta = fields.Date(string="Cumplido hasta", tracking=True,
                                 help="En los repetitivos: los vencimientos hasta esta fecha ya están registrados.")
    nota = fields.Text(string="Nota")
    active = fields.Boolean(default=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related="company_id.currency_id")

    @api.constrains("importe")
    def _check_importe(self):
        for c in self:
            if c.importe <= 0:
                raise ValidationError("El importe va en positivo; el tipo indica si entra o sale.")

    def yg_vencimientos(self, desde, hasta):
        """[(compromiso, fecha, importe con signo)] de los vencimientos pendientes hasta «hasta».
        Los anteriores a «desde» se devuelven igual: el informe los trata como vencidos."""
        resultado = []
        for c in self.filtered(lambda x: not x.cumplido):
            signo = 1 if c.tipo == "ingreso" else -1
            fecha = c.fecha_vencimiento
            fin = min(c.hasta or hasta, hasta) if c.recurrencia != "unica" else fecha
            paso = relativedelta(weeks=1) if c.recurrencia == "semanal" else relativedelta(months=1)
            n = 0
            while fecha <= fin and fecha <= hasta:
                if not c.cumplido_hasta or fecha > c.cumplido_hasta:
                    resultado.append((c, fecha, signo * c.importe))
                if c.recurrencia == "unica":
                    break
                n += 1
                fecha = c.fecha_vencimiento + paso * n
        return resultado
