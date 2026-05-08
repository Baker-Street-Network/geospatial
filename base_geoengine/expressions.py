# Copyright 2023 ACSONE SA/NV
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
#
# Local Odoo 19 port of base_geoengine's geo-operator hook (OCA's 19.0
# branch of OCA/geospatial does not yet contain base_geoengine, so this
# file lives here as a project-local fork). v18-style BaseModel-level
# monkey-patching no longer works because Odoo 19 moved
# `_condition_to_sql` from BaseModel to Field, with a new signature.
# See thoughts/shared/implementation/2026-05-05-fix-stuck-to-upgrade-modules.md

import random
import string

from odoo.orm.domains import CONDITION_OPERATORS
from odoo.orm.utils import SQL_OPERATORS
from odoo.osv import expression
from odoo.tools import SQL, Query

from .fields import GeoField
from .geo_operators import GeoOperator

GEO_OPERATORS = {
    "geo_greater": ">",
    "geo_lesser": "<",
    "geo_equal": "=",
    "geo_touch": "ST_Touches",
    "geo_within": "ST_Within",
    "geo_contains": "ST_Contains",
    "geo_intersect": "ST_Intersects",
}
GEO_SQL_OPERATORS = {
    "geo_greater": SQL(">"),
    "geo_lesser": SQL("<"),
    "geo_equal": SQL("="),
    "geo_touch": SQL("ST_Touches"),
    "geo_within": SQL("ST_Within"),
    "geo_contains": SQL("ST_Contains"),
    "geo_intersect": SQL("ST_Intersects"),
}

# Register custom operators with the v19 ORM. CONDITION_OPERATORS is the
# canonical mutable registry used by Domain validation; SQL_OPERATORS is the
# rendering map used by Field._condition_to_sql.
CONDITION_OPERATORS.update(GEO_OPERATORS.keys())
SQL_OPERATORS.update(GEO_SQL_OPERATORS)

# Older code in this module references expression.TERM_OPERATORS /
# expression.SQL_OPERATORS; keep those in sync for backwards compatibility
# with any downstream that still reads them.
if hasattr(expression, "TERM_OPERATORS"):
    expression.TERM_OPERATORS = tuple(set(expression.TERM_OPERATORS) | set(GEO_OPERATORS))
if hasattr(expression, "SQL_OPERATORS"):
    expression.SQL_OPERATORS.update(GEO_SQL_OPERATORS)


_original_field_condition_to_sql = GeoField._condition_to_sql


def _geo_condition_to_sql(
    self, field_expr: str, operator: str, value, model, alias: str, query: Query
) -> SQL:
    """Override Field._condition_to_sql on GeoField to support geo_* operators.

    Falls through to the standard Field implementation for non-geo operators.
    """
    if operator not in GEO_OPERATORS:
        return _original_field_condition_to_sql(
            self, field_expr, operator, value, model, alias, query
        )

    fname = field_expr if field_expr == self.name else self.name
    current_operator = GeoOperator(self)
    params: list = []

    if isinstance(value, dict):
        # Indirect geo_operator like
        # ('geom', 'geo_within', {'res.zip.poly': [('id', 'in', [1,2,3])]})
        ref_search = value
        sub_queries = []
        for key in ref_search:
            i = key.rfind(".")
            rel_model_name = key[0:i]
            rel_col = key[i + 1 :]
            rel_model = model.env[rel_model_name]
            if not ref_search[key]:
                continue
            rel_alias = (
                rel_model._table
                + "_"
                + "".join(random.choices(string.ascii_lowercase, k=5))
            )
            rel_query = where_calc(
                rel_model,
                ref_search[key],
                active_test=True,
                alias=rel_alias,
            )
            rel_model._apply_ir_rules(rel_query, "read")
            if operator == "geo_equal":
                rel_query.add_where(
                    f'"{alias}"."{fname}" {GEO_OPERATORS[operator]} '
                    f"{rel_alias}.{rel_col}"
                )
            elif operator in ("geo_greater", "geo_lesser"):
                rel_query.add_where(
                    f"ST_Area({alias}.{fname}) {GEO_OPERATORS[operator]} "
                    f"ST_Area({rel_alias}.{rel_col})"
                )
            else:
                rel_query.add_where(
                    f'{GEO_OPERATORS[operator]}("{alias}"."{fname}", '
                    f"{rel_alias}.{rel_col})"
                )
            subquery, subparams = rel_query.subselect("1")
            sub_query_mogrified = (
                model.env.cr.mogrify(subquery, subparams)
                .decode("utf-8")
                .replace(f"'{rel_model._table}'", f'"{rel_model._table}"')
                .replace("%", "%%")
            )
            sub_queries.append(f"EXISTS({sub_query_mogrified})")
        sql_str = " AND ".join(sub_queries)
    else:
        sql_str = get_geo_func(
            current_operator, operator, fname, value, params, alias
        )
    return SQL(sql_str, *params)


GeoField._condition_to_sql = _geo_condition_to_sql


def get_geo_func(current_operator, operator, left, value, params, table):
    """
    This method will call the SQL query corresponding to the requested geo operator
    """
    match operator:
        case "geo_greater":
            query = current_operator.get_geo_greater_sql(table, left, value, params)
        case "geo_lesser":
            query = current_operator.get_geo_lesser_sql(table, left, value, params)
        case "geo_equal":
            query = current_operator.get_geo_equal_sql(table, left, value, params)
        case "geo_touch":
            query = current_operator.get_geo_touch_sql(table, left, value, params)
        case "geo_within":
            query = current_operator.get_geo_within_sql(table, left, value, params)
        case "geo_contains":
            query = current_operator.get_geo_contains_sql(table, left, value, params)
        case "geo_intersect":
            query = current_operator.get_geo_intersect_sql(table, left, value, params)
        case _:
            raise NotImplementedError(f"The operator {operator} is not supported")
    return query


def where_calc(model, domain, active_test=True, alias=None):
    """
    This method is copied from base, we need to create our own query.
    """
    # if the object has an active field ('active', 'x_active'), filter out all
    # inactive records unless they were explicitly asked for
    if model._active_name and active_test and model._context.get("active_test", True):
        if not any(item[0] == model._active_name for item in domain):
            domain = [(model._active_name, "=", 1)] + domain

    query = Query(model.env, alias, model._table)
    if domain:
        return expression.expression(domain, model, alias=alias, query=query).query
    return query
