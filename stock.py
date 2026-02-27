# The COPYRIGHT file at the top level of this repository contains the full
# copyright notices and license terms.
from datetime import datetime
from trytond.model import fields, ModelView
from trytond.pool import Pool
from trytond.pyson import Bool, Eval, If
from trytond.wizard import Wizard, StateView, StateReport, Button
from trytond.transaction import Transaction
from trytond.modules.html_report.dominate_report import DominateReport
from trytond.modules.html_report.engine import DualRecord
from trytond.url import http_host
from trytond.modules.html_report.i18n import _
from dominate.util import raw
from dominate.tags import (a, button, div, h1, i, script, strong, table, tbody,
    td, th, thead, tr)


class PrintStockMoveLocationStart(ModelView):
    'Print Stock Move Location Start'
    __name__ = 'stock.move.location.start'
    from_date = fields.Date('From Date',
        domain = [
            If(Bool(Eval('from_date')) & Bool(Eval('to_date')),
                ('from_date', '<=', Eval('to_date')), ())],
        states={
            'required': Bool(Eval('to_date', False)),
        })
    to_date = fields.Date('To Date',
        domain = [
            If(Bool(Eval('from_date')) & Bool(Eval('to_date')),
                ('from_date', '<=', Eval('to_date')), ())],
        states={
            'required': Bool(Eval('from_date', False)),
        })
    warehouse = fields.Many2One('stock.location', 'Warehouse',
        required=True, domain=[('type', '=', 'warehouse')])

    @classmethod
    def default_warehouse(cls):
        Location = Pool().get('stock.location')
        locations = Location.search(cls.warehouse.domain)
        if len(locations) == 1:
            return locations[0].id


class PrintStockMoveLocation(Wizard):
    'Print Stock Move Location'
    __name__ = 'stock.print_stock_move_location'
    start = StateView('stock.move.location.start',
        'stock_move_location_report.print_stock_move_location_start_view_form', [
            Button('Cancel', 'end', 'tryton-cancel'),
            Button('Print', 'print_', 'tryton-print', default=True),
            ])
    print_ = StateReport('stock.move.location.report')

    def do_print_(self, action):
        context = Transaction().context
        data = {
            'from_date': self.start.from_date,
            'to_date': self.start.to_date,
            'warehouse': self.start.warehouse.id,
            'model': context.get('active_model'),
            'ids': context.get('active_ids'),
            }
        return action, data


class PrintStockMoveLocationReport(DominateReport):
    __name__ = 'stock.move.location.report'

    @classmethod
    def prepare(cls, data):
        pool = Pool()
        Template = pool.get('product.template')
        Product = pool.get('product.product')
        Move = pool.get('stock.move')
        Location = pool.get('stock.location')
        Company = pool.get('company.company')

        try:
            Production = pool.get('production')
        except:
            Production = None
        try:
            Lot = pool.get('stock.lot')
        except:
            Lot = None

        move = Move.__table__()
        cursor = Transaction().connection.cursor()

        t_context = Transaction().context
        company_id = t_context.get('company')
        from_date = data.get('from_date') or datetime.min.date()
        to_date = data.get('to_date') or datetime.max.date()
        warehouse = Location(data.get('warehouse'))

        parameters = {}
        parameters['from_date'] = from_date
        parameters['to_date'] = to_date
        parameters['warehouse'] = warehouse.rec_name
        parameters['show_date'] = True if data.get('from_date') else False
        parameters['production'] = True if Production else False
        parameters['lot'] = True if Lot else False
        parameters['base_url'] = '%s/#%s' % (http_host(),
            Transaction().database.name)
        parameters['company'] = (DualRecord(Company(company_id))
            if company_id is not None and company_id >= 0 else None)

        # Locations
        locations = [l.id for l in Location.search([
            ('parent', 'child_of', [warehouse.id])])]
        location_suppliers = [l.id for l in Location.search(
            [('type', '=', 'supplier')])]
        location_customers = [l.id for l in Location.search([
            ('type', '=', 'customer')])]
        location_lost_founds = [l.id for l in Location.search([
            ('type', '=', 'lost_found')])]
        location_productions = [l.id for l in Location.search([
            ('type', '=', 'production')])] if Production else []

        keys = ()
        if data.get('model') == 'product.template':
            grouping = ('product',)
            for template in Template.browse(data['ids']):
                for product in template.products:
                    keys += ((product, None),)
        elif data.get('model') == 'product.product':
            grouping = ('product',)
            for product in Product.browse(data['ids']):
                keys += ((product, None),)
        elif data.get('model') == 'stock.lot':
            grouping = ('product', 'lot')
            Lot = pool.get('stock.lot')
            for lot in Lot.browse(data['ids']):
                keys += ((lot.product, lot),)

        def compute_quantites(sql_where):
            Uom = Pool().get('product.uom')

            query = move.select(move.id.as_('move_id'), where=sql_where,
                order_by=move.effective_date.desc)
            cursor.execute(*query)
            move_ids = [m[0] for m in cursor.fetchall()]
            moves = Move.browse(move_ids)
            total = sum([Uom.compute_qty(
                            m.unit, m.quantity, m.product.default_uom, True)
                            for m in moves])
            moves = [DualRecord(m) for m in moves]
            return total, moves

        records = []
        for key in keys:
            product = key[0]
            lot = key[1]

            # Initial stock
            context = {}
            context['stock_date_end'] = from_date
            with Transaction().set_context(context):
                pbl = Product.products_by_location([warehouse.id],
                    with_childs=True,
                    grouping_filter=([product.id],),
                    grouping=grouping)
            key = ((warehouse.id, product.id, lot.id)
                if lot else (warehouse.id, product.id))
            initial_stock = pbl.get(key, 0)

            sql_common_where = ((move.product == product.id)
                & (move.effective_date >= from_date)
                    & (move.effective_date <= to_date)
                & (move.state == 'done') & (move.company == company_id))
            if lot:
                sql_common_where &= (move.lot == lot.id)

            # supplier_incommings from_location = supplier
            sql_where = (sql_common_where
                & (move.from_location.in_(location_suppliers))
                & (move.to_location.in_(locations)))
            supplier_incommings_total, supplier_incommings = compute_quantites(sql_where)

            # supplier_returns: to_location = supplier
            sql_where = (sql_common_where
                & (move.to_location.in_(location_suppliers))
                & (move.from_location.in_(locations)))
            supplier_returns_total, supplier_returns = compute_quantites(sql_where)

            # customer_outgoing: to_location = customer
            sql_where = (sql_common_where
                & (move.to_location.in_(location_customers))
                & (move.from_location.in_(locations)))
            customer_outgoings_total, customer_outgoings = compute_quantites(sql_where)

            # customer_return: from_location = customer
            sql_where = (sql_common_where
                & (move.from_location.in_(location_customers))
                & (move.to_location.in_(locations)))
            customer_returns_total, customer_returns = compute_quantites(sql_where)

            production_outs_total = 0
            production_ins_total = 0
            if location_productions:
                # production_outs: to_location = production
                sql_where = (sql_common_where
                    & (move.from_location.in_(location_productions))
                    & (move.to_location.in_(locations)))
                production_outs_total, production_outs = compute_quantites(sql_where)

                # production_ins: from_location = production
                sql_where = (sql_common_where
                    & (move.to_location.in_(location_productions))
                    & (move.from_location.in_(locations)))
                production_ins_total, production_ins = compute_quantites(sql_where)

            # inventory
            sql_where = (sql_common_where
                & (move.from_location.in_(location_lost_founds))
                & (move.to_location.in_(locations)))
            lost_found_from_total, lost_found_from = compute_quantites(sql_where)

            sql_where = (sql_common_where
                & (move.to_location.in_(location_lost_founds))
                & (move.from_location.in_(locations)))
            lost_found_to_total, lost_found_to = compute_quantites(sql_where)

            # Entries from outside warehouse
            locations_in_out = (locations + location_lost_founds
                + location_suppliers + location_customers)
            if location_productions:
                locations_in_out += location_productions

            sql_where = (sql_common_where
                & (~move.from_location.in_(locations_in_out))
                & (move.to_location.in_(locations)))
            in_to_total, in_to = compute_quantites(sql_where)

            # Outputs from our warehouse
            sql_where = (sql_common_where
                & (move.from_location.in_(locations))
                & (~move.to_location.in_(locations_in_out)))
            out_to_total, out_to = compute_quantites(sql_where)

            records.append({
                'product': DualRecord(product),
                'lot': DualRecord(lot),
                'initial_stock': initial_stock,
                'supplier_incommings_total': supplier_incommings_total,
                'supplier_incommings': supplier_incommings,
                'supplier_returns_total': (-supplier_returns_total
                    if supplier_returns_total else 0),
                'supplier_returns': supplier_returns,
                'customer_outgoings_total': (
                    -customer_outgoings_total if customer_outgoings_total else 0),
                'customer_outgoings': customer_outgoings,
                'customer_returns_total': customer_returns_total,
                'customer_returns': customer_returns,
                'production_outs_total': (production_outs_total
                    if Production else 0),
                'production_outs': production_outs if Production else 0,
                'production_ins_total': (-production_ins_total
                    if Production else 0),
                'production_ins': production_ins if Production else 0,
                'lost_found_total':
                    lost_found_from_total - lost_found_to_total,
                'lost_found_from_total': lost_found_from_total,
                'lost_found_from': lost_found_from,
                'lost_found_to_total': (-lost_found_to_total
                    if lost_found_to_total else 0),
                'lost_found_to': lost_found_to,
                'in_to_total': in_to_total if in_to_total else 0,
                'in_to': in_to,
                'out_to_total': -out_to_total if out_to_total else 0,
                'out_to': out_to,
                'total': (initial_stock + supplier_incommings_total
                    + (-supplier_returns_total) + (-customer_outgoings_total)
                    + customer_returns_total + production_outs_total
                    + (-production_ins_total) + (lost_found_from_total - lost_found_to_total)
                    + in_to_total + (-out_to_total)),
                })
        return records, parameters

    @classmethod
    def _origin(cls, record, parameters):
        origin = str(record.raw.origin)
        model, id_ = origin.split(',')
        label = _('Origin')
        if model == 'sale.line':
            label = _('Sale Line')
        if model == 'purhcase.line':
            label = _('Purchase Line')
        if model == 'stock.move':
            label = _('Move')
        if model == 'production':
            label = _('Production')
        return a(record.origin.render.rec_name,
            href='%s/model/%s/%s;name="%s"' % (
                parameters['base_url'], model, id_, label))

    @classmethod
    def _draw_table_shipment(cls, key, records, parameters):
        table_attrs = {'cls': 'table collapse multi-collapse', 'id': key}
        detail_table = table(**table_attrs)
        with detail_table:
            with thead():
                with tr():
                    if parameters.get('lot'):
                        th(_('Lot'), scope='col')
                    th(_('Quantity'), scope='col')
                    th(_('UdM'), scope='col')
                    th(_('Origin'), scope='col')
                    th(_('Effective Date'), scope='col')
                    th(_('Warehouse'), scope='col')
                    th('', scope='col')
            with tbody():
                for record in records:
                    with tr():
                        if parameters.get('lot'):
                            with td():
                                if getattr(record.raw, 'lot', None):
                                    a(record.lot.render.number,
                                        href='%s/model/stock.lot/%s;name="%s"' % (
                                            parameters['base_url'],
                                            record.lot.raw.id,
                                            _('Lots')))
                        td(record.render.quantity)
                        td(record.uom.render.symbol)
                        with td() as origin_cell:
                            if record.origin:
                                origin_cell.add(cls._origin(record, parameters))
                        td(record.render.effective_date)
                        td(record.shipment.warehouse.render.rec_name
                            if record.shipment and record.shipment.warehouse else '')
                        with td():
                            a(i(cls='fas fa-arrow-right'),
                                href='%s/model/stock.move/%s;name="%s"' % (
                                    parameters['base_url'],
                                    record.raw.id,
                                    _('Move')))
        return detail_table

    @classmethod
    def _draw_table_production(cls, key, in_out, records, parameters):
        detail_table = table(cls='table collapse multi-collapse', id=key)
        with detail_table:
            with thead():
                with tr():
                    if parameters.get('lot'):
                        th(_('Lot'), scope='col')
                    th(_('Quantity'), scope='col')
                    th(_('UdM'), scope='col')
                    th(_('Origin'), scope='col')
                    th(_('Effective Date'), scope='col')
                    th(_('Warehouse'), scope='col')
                    th('', scope='col')
            with tbody():
                for record in records:
                    production = getattr(record, in_out)
                    with tr():
                        if parameters.get('lot'):
                            with td():
                                if getattr(record.raw, 'lot', None):
                                    a(record.lot.render.number,
                                        href='%s/model/stock.lot/%s;name="%s"' % (
                                            parameters['base_url'],
                                            record.lot.raw.id,
                                            _('Lots')))
                        with td():
                            a(record.render.quantity,
                                href='%s/model/stock.move/%s;name="%s"' % (
                                    parameters['base_url'],
                                    record.raw.id,
                                    _('Move')))
                        td(record.uom.render.symbol)
                        with td() as origin_cell:
                            if record.origin:
                                origin_cell.add(cls._origin(record, parameters))
                        td(record.render.effective_date)
                        td(production.warehouse.render.rec_name)
                        with td():
                            a(i(cls='fas fa-arrow-right'),
                                href='%s/model/stock.move/%s;name="%s"' % (
                                    parameters['base_url'],
                                    record.raw.id,
                                    _('Move')))
        return detail_table

    @classmethod
    def _draw_table(cls, key, records, parameters):
        attrs = {'cls': 'table collapse multi-collapse', 'id': key} if key else {'cls': 'table'}
        detail_table = table(**attrs)
        with detail_table:
            with thead():
                with tr():
                    if parameters.get('lot'):
                        th(_('Lot'), scope='col')
                    th(_('Quantity'), scope='col')
                    th(_('UdM'), scope='col')
                    th(_('Origin'), scope='col')
                    th(_('Effective Date'), scope='col')
                    th('', scope='col')
            with tbody():
                for record in records:
                    with tr():
                        if parameters.get('lot'):
                            with td():
                                if getattr(record.raw, 'lot', None):
                                    a(record.lot.render.number,
                                        href='%s/model/stock.lot/%s;name="%s"' % (
                                            parameters['base_url'],
                                            record.lot.raw.id,
                                            _('Lots')))
                        with td():
                            a(record.render.quantity,
                                href='%s/model/stock.move/%s;name="%s"' % (
                                    parameters['base_url'],
                                    record.raw.id,
                                    record.render.rec_name))
                        td(record.uom.render.symbol)
                        with td() as origin_cell:
                            if record.origin:
                                origin_cell.add(cls._origin(record, parameters))
                        td(record.render.effective_date)
                        with td():
                            a(i(cls='fas fa-arrow-right'),
                                href='%s/model/stock.move/%s;name="%s"' % (
                                    parameters['base_url'],
                                    record.raw.id,
                                    _('Move')))
        return detail_table

    @classmethod
    def css(cls, action, data, records):
        return "\n".join([
            "@import url('https://stackpath.bootstrapcdn.com/bootstrap/4.3.1/css/bootstrap.min.css');",
            "@import url('https://use.fontawesome.com/releases/v5.7.0/css/all.css');",
            ])

    @classmethod
    def title(cls, action, data, records):
        return _('Stock Move Location')

    @classmethod
    def body(cls, action, data, records):
        parameters = data['parameters']
        render = cls.render
        wrapper = div()
        with wrapper:
            with table(cls='table'):
                with tbody():
                    with tr():
                        with td():
                            h1(_('Stock Move Location'))
                        with td(align='right'):
                            company = parameters.get('company')
                            if company:
                                a(company.render.rec_name,
                                    href=parameters['base_url'],
                                    alt=company.render.rec_name)
                            button(_('Expand All'),
                                type='button',
                                cls='btn tn-outline-light btn-sm',
                                onclick='expand()')
                    if parameters.get('show_date'):
                        with tr():
                            with td():
                                strong(_('From Date:'))
                                raw(' %s' % render(parameters['from_date']))
                            with td():
                                strong(_('To Date:'))
                                raw(' %s' % render(parameters['to_date']))
                    for record in data['records']:
                        with tr():
                            with td():
                                strong(_('Product:'))
                                raw(' %s' % record['product'].render.rec_name)
                            with td():
                                if record['lot']:
                                    strong(_('Lot:'))
                                    raw(' %s' % record['lot'].render.number)
                        with tr():
                            with td():
                                strong(_('Warehouse:'))
                                raw(' %s' % parameters['warehouse'])
                            td('')
                        with tr():
                            td(_('Initial Stock'))
                            td(render(record['initial_stock']))
                        with tr():
                            with td():
                                with a(href='#supplier-incommings',
                                    cls='',
                                    **{
                                        'data-toggle': 'collapse',
                                        'role': 'button',
                                        'aria-expanded': 'false',
                                        'aria-controls': 'supplier-incommings',
                                    }):
                                    i(cls='fas fa-angle-double-right')
                                    raw(' ' + _('Supplier Incomming'))
                            td('%s %s' % (
                                render(record['supplier_incommings_total']),
                                record['product'].default_uom.render.symbol))
                        with tr():
                            with td(colspan='2') as detail_cell:
                                detail_cell.add(cls._draw_table_shipment(
                                    'supplier-incommings',
                                    record['supplier_incommings'],
                                    parameters))
                        with tr():
                            with td():
                                with a(href='#supplier-returns',
                                    cls='',
                                    **{
                                        'data-toggle': 'collapse',
                                        'role': 'button',
                                        'aria-expanded': 'false',
                                        'aria-controls': 'supplier-returns',
                                    }):
                                    i(cls='fas fa-angle-double-right')
                                    raw(' ' + _('Supplier Returns'))
                            td('%s %s' % (
                                render(record['supplier_returns_total']),
                                record['product'].default_uom.render.symbol))
                        with tr():
                            with td(colspan='2') as detail_cell:
                                detail_cell.add(cls._draw_table(
                                    'supplier-returns',
                                    record['supplier_returns'],
                                    parameters))
                        with tr():
                            with td():
                                with a(href='#customer-outgoings',
                                    cls='',
                                    **{
                                        'data-toggle': 'collapse',
                                        'role': 'button',
                                        'aria-expanded': 'false',
                                        'aria-controls': 'customer-outgoings',
                                    }):
                                    i(cls='fas fa-angle-double-right')
                                    raw(' ' + _('Customer Outgoings'))
                            td('%s %s' % (
                                render(record['customer_outgoings_total']),
                                record['product'].default_uom.render.symbol))
                        with tr():
                            with td(colspan='2') as detail_cell:
                                detail_cell.add(cls._draw_table_shipment(
                                    'customer-outgoings',
                                    record['customer_outgoings'],
                                    parameters))
                        with tr():
                            with td():
                                with a(href='#customer-returns',
                                    cls='',
                                    **{
                                        'data-toggle': 'collapse',
                                        'role': 'button',
                                        'aria-expanded': 'false',
                                        'aria-controls': 'customer-returns',
                                    }):
                                    i(cls='fas fa-angle-double-right')
                                    raw(' ' + _('Customer Returns'))
                            td('%s %s' % (
                                render(record['customer_returns_total']),
                                record['product'].default_uom.render.symbol))
                        with tr():
                            with td(colspan='2') as detail_cell:
                                detail_cell.add(cls._draw_table_shipment(
                                    'customer-returns',
                                    record['customer_returns'],
                                    parameters))
                        if parameters.get('production'):
                            with tr():
                                with td():
                                    with a(href='#production-outs',
                                        cls='',
                                        **{
                                            'data-toggle': 'collapse',
                                            'role': 'button',
                                            'aria-expanded': 'false',
                                            'aria-controls': 'production-outs',
                                        }):
                                        i(cls='fas fa-angle-double-right')
                                        raw(' ' + _('Production Out'))
                                td('%s %s' % (
                                    render(record['production_outs_total']),
                                    record['product'].default_uom.render.symbol))
                            with tr():
                                with td(colspan='2') as detail_cell:
                                    detail_cell.add(cls._draw_table_production(
                                        'production-outs',
                                        'production_output',
                                        record['production_outs'],
                                        parameters))
                            with tr():
                                with td():
                                    with a(href='#production-ins',
                                        cls='',
                                        **{
                                            'data-toggle': 'collapse',
                                            'role': 'button',
                                            'aria-expanded': 'false',
                                            'aria-controls': 'production-ins',
                                        }):
                                        i(cls='fas fa-angle-double-right')
                                        raw(' ' + _('Production In'))
                                td('%s %s' % (
                                    render(record['production_ins_total']),
                                    record['product'].default_uom.render.symbol))
                            with tr():
                                with td(colspan='2') as detail_cell:
                                    detail_cell.add(cls._draw_table_production(
                                        'production-ins',
                                        'production_input',
                                        record['production_ins'],
                                        parameters))
                        with tr():
                            with td():
                                with a(href='#inventory',
                                    cls='',
                                    **{
                                        'data-toggle': 'collapse',
                                        'role': 'button',
                                        'aria-expanded': 'false',
                                        'aria-controls': 'inventory',
                                    }):
                                    i(cls='fas fa-angle-double-right')
                                    raw(' ' + _('Inventory'))
                            td('%s %s' % (
                                render(record['lost_found_total']),
                                record['product'].default_uom.render.symbol))
                        with tr():
                            with td(colspan='2'):
                                with table(cls='table collapse multi-collapse',
                                        id='inventory'):
                                    if record['lost_found_from']:
                                        with tr():
                                            with td():
                                                i(cls='fas fa-angle-double-right')
                                                raw(' ' + _('From Lost & Found'))
                                            td('%s %s' % (
                                                render(record['lost_found_from_total']),
                                                record['product'].default_uom.render.symbol))
                                        with tr():
                                            with td(colspan='2') as detail_cell:
                                                detail_cell.add(cls._draw_table(
                                                    '',
                                                    record['lost_found_from'],
                                                    parameters))
                                    if record['lost_found_to']:
                                        with tr():
                                            with td():
                                                i(cls='fas fa-angle-double-right')
                                                raw(' ' + _('To Lost & Found'))
                                            td('%s %s' % (
                                                render(record['lost_found_to_total']),
                                                record['product'].default_uom.render.symbol))
                                        with tr():
                                            with td(colspan='2') as detail_cell:
                                                detail_cell.add(cls._draw_table(
                                                    '',
                                                    record['lost_found_to'],
                                                    parameters))
                        with tr():
                            with td():
                                with a(href='#in-to',
                                    cls='',
                                    **{
                                        'data-toggle': 'collapse',
                                        'role': 'button',
                                        'aria-expanded': 'false',
                                        'aria-controls': 'in-to',
                                    }):
                                    i(cls='fas fa-angle-double-right')
                                    raw(' ' + _('Entries from outside warehouse'))
                            td('%s %s' % (
                                render(record['in_to_total']),
                                record['product'].default_uom.render.symbol))
                        with tr():
                            with td(colspan='2') as detail_cell:
                                detail_cell.add(cls._draw_table(
                                    'in-to',
                                    record['in_to'],
                                    parameters))
                        with tr():
                            with td():
                                with a(href='#out-to',
                                    cls='',
                                    **{
                                        'data-toggle': 'collapse',
                                        'role': 'button',
                                        'aria-expanded': 'false',
                                        'aria-controls': 'out-to',
                                    }):
                                    i(cls='fas fa-angle-double-right')
                                    raw(' ' + _('Outputs from our warehouse'))
                            td('%s %s' % (
                                render(record['out_to_total']),
                                record['product'].default_uom.render.symbol))
                        with tr():
                            with td(colspan='2') as detail_cell:
                                detail_cell.add(cls._draw_table(
                                    'out-to',
                                    record['out_to'],
                                    parameters))
                        with tr():
                            td(_('Total'))
                            td('%s %s' % (
                                render(record['total']),
                                record['product'].default_uom.render.symbol))
            script(src='https://code.jquery.com/jquery-3.3.1.slim.min.js',
                integrity='sha384-q8i/X+965DzO0rT7abK41JStQIAqVgRVzpbzo5smXKp4YfRvH+8abtTE1Pi6jizo',
                crossorigin='anonymous')
            script(src='https://cdnjs.cloudflare.com/ajax/libs/popper.js/1.14.7/umd/popper.min.js',
                integrity='sha384-UO2eT0CpHqdSJQ6hJty5KVphtPhzWj9WO1clHTMGa3JDZwrnQq4sF86dIHNDz0W1',
                crossorigin='anonymous')
            script(src='https://stackpath.bootstrapcdn.com/bootstrap/4.3.1/js/bootstrap.min.js',
                integrity='sha384-JjSmVgyd0p3pXB1rRibZUAYoIIy6OrQ6VrjIEaFf/nJGzIxFDsf4x0xIM+B07jRM',
                crossorigin='anonymous')
            script(raw("""
function expand() {
  $('.collapse').collapse('show');
}
"""), type='text/javascript', charset='utf-8')
        return wrapper

    @classmethod
    def execute(cls, ids, data):
        records, parameters = cls.prepare(data)
        return super().execute(ids, {
            'name': 'stock.move.location.report',
            'model': data['model'],
            'records': records,
            'parameters': parameters,
            'output_format': 'html',
            'report_options': {
                'now': datetime.now(),
                }
            })
