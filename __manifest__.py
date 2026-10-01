# Yagüven C.G. — Licencia LGPL-3.
{
    'name': 'Flujo de fondos proyectado',
    'version': '20.0.1.1.0',
    'summary': 'Proyección de caja por fecha de vencimiento: cuotas y facturas abiertas más compromisos futuros cargados a mano',
    'description': """
Flujo de fondos proyectado
==========================
* Saldo inicial de caja y bancos (según extracto bancario o según contabilidad).
* Facturas, cuotas y planes de pago abiertos, cada uno en su fecha de vencimiento; lo vencido va al primer período.
* Compromisos futuros cargados a mano (sueldos, alquiler, cobranzas esperadas), únicos o repetitivos, con fecha de vencimiento.
* Columnas por día, por semana o por mes; saldo acumulado y «A cubrir»: lo que hace falta aportar para no quedar en negativo.
* Se ve en el visor de informes (yg_account_reports), con despliegue por comprobante, PDF y Excel.
    """,
    'author': 'Yagüven C.G.',
    'website': 'https://yaguven.com',
    'category': 'Accounting/Accounting',
    'license': 'LGPL-3',
    'depends': ['account', 'yg_account_reports'],
    'data': [
        'security/ir.access.csv',
        'views/yg_flujo_compromiso_views.xml',
        'data/yg_flujo_informe.xml',
    ],
    'installable': True,
    'application': False,
}
