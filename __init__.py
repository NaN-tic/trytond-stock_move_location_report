# The COPYRIGHT file at the top level of this repository contains the full
# copyright notices and license terms.
from trytond.pool import Pool
from . import stock


def register():
    Pool.register(
        stock.PrintStockMoveLocationStart,
        module='stock_move_location_report', type_='model')
    Pool.register(
        stock.PrintStockMoveLocation,
        module='stock_move_location_report', type_='wizard')
    Pool.register(
        stock.PrintStockMoveLocationReport,
        module='stock_move_location_report', type_='report')
