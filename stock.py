# The COPYRIGHT file at the top level of this repository contains the full
# copyright notices and license terms.
import os
from datetime import datetime
from trytond.config import config
from trytond.model import fields, ModelView
from trytond.pool import Pool
from trytond.pyson import Bool, Eval, If
from trytond.wizard import Wizard, StateView, StateReport, Button
from trytond.transaction import Transaction
from trytond.modules.html_report.html_report import HTMLReport
from trytond.modules.html_report.engine import DualRecord


__all__ = ['PrintStockMoveLocationStart', 'PrintStockMoveLocation',
    'PrintStockMoveLocationReport']

BASE_URL = config.get('web', 'base_url')


class PrintStockMoveLocationStart(ModelView):
    'Print Stock Move Location Start'
    __name__ = 'stock.move.location.start'
    from_date = fields.Date('From Date',
        domain = [
            If(Bool(Eval('from_date')) & Bool(Eval('to_date')),
                ('from_date', '<=', Eval('to_date')), ())],
        states={
            'required': Bool(Eval('to_date', False)),
        }, depends=['to_date'])
    to_date = fields.Date('To Date',
        domain = [
            If(Bool(Eval('from_date')) & Bool(Eval('to_date')),
                ('from_date', '<=', Eval('to_date')), ())],
        states={
            'required': Bool(Eval('from_date', False)),
        }, depends=['from_date'])
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


class PrintStockMoveLocationReport(HTMLReport):
    __name__ = 'stock.move.location.report'

    @classmethod
    def get_context(cls, records, data):
        pool = Pool()
        Company = pool.get('company.company')
        t_context = Transaction().context

        context = super().get_context(records, data)
        context['company'] = Company(t_context['company'])
        return context

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
        # TODO get url from trytond.url issue8767
        if BASE_URL:
            base_url = '%s/#%s' % (
                BASE_URL, Transaction().database.name)
        elif t_context.get('_request'):
            base_url = '%s://%s/#%s' % (
                t_context['_request']['scheme'],
                t_context['_request']['http_host'],
                Transaction().database.name
                )
        else:
            base_url = None

        parameters['base_url'] = base_url
        parameters['company'] = DualRecord(Company(company_id))

        # Locations
        locations = [l.id for l in Location.search([
            ('parent', 'child_of', [warehouse.id])])]
        location_suppliers = [l.id for l in Location.search(
            [('type', '=', 'supplier')])]
        location_customers = [l.id for l in Location.search([
            ('type', '=', 'customer')])]
        location_lost_founds = [l.id for l in Location.search([
            ('type', '=', 'lost_found')])]
        if Production:
            location_productions = [l.id for l in Location.search([
                ('type', '=', 'production')])]

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
                            m.uom, m.quantity, m.product.default_uom, True)
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
            if Production:
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
            if Production:
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
    def execute(cls, ids, data):
        context = Transaction().context
        context['report_lang'] = Transaction().language
        context['report_translations'] = os.path.join(
            os.path.dirname(__file__), 'report', 'translations')

        with Transaction().set_context(**context):
            records, parameters = cls.prepare(data)
            return super(PrintStockMoveLocationReport, cls).execute(ids, {
                    'name': 'stock.move.location.report',
                    'model': data['model'],
                    'records': records,
                    'parameters': parameters,
                    'output_format': 'html',
                    'report_options': {
                        'now': datetime.now(),
                        }
                    })
