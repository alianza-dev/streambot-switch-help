import os
from copy import copy
from streaming_lib.utils.lambdas import safe_list, safe_dict
from streaming_lib.utils.structural import Struct
from streaming_lib.lambdas.base_daily_snapshotter import BaseDailySnapshotter
from streaming_lib.utils.context_printer import context_decorator


class DailySnapshotter(BaseDailySnapshotter):

    @context_decorator
    def define_projection_sql(self, table):
        """ Define the projection sql for projecting a daily snapshot of streamed entities"""
        self.ctx.push()

        create = Struct.make(safe_dict(table.create))
        join_sql_ab = self._sql_join_clause(table.keys, 'a', 'b')
        join_sql_ac = self._sql_join_clause(table.keys, 'a', 'c')
        col_list_with_subs = self._sql_column_list_with_table_subs(table)
        col_list_a_sans_create = self._sql_column_list(table, 'a', redacts=[create.get('col')])
        key_list_a = self._sql_column_list(table.keys, 'a')
        sequence_list_a = self._sql_column_list(table.sequence_cols, 'a', table.sequence_ops)
        sequence_list_inverted_a = self._sql_column_list(table.sequence_cols, 'a', table.sequence_ops, invert_ops=True)

        table.projection_sql = f"""
            with stream as (
                select
                    row_number() over (partition by {key_list_a} order by {sequence_list_a}) as latest_rownum
                    , row_number() over (partition by {key_list_a} order by {sequence_list_inverted_a}) as first_rownum
                    , a.*
                from
                    {self.stream_database}."{table.name}" a
                where true
                    and date(concat(a._year, '-', a._month, '-', a._day)) between date('{{start_date}}') and date('{{end_date}}')
            ), streamwithcreate as (
                select
                    {col_list_a_sans_create}
                    {", coalesce(b.{0}, c.{0}) as {0}, ".format(create.col) if create else ""}
                    , a._year
                    , a._month
                    , a._day
                from
                    stream a
                    {"left join stream b on {0} and b._metadata.action = '{1}' and b.first_rownum = 1".format(join_sql_ab, create.action) if create else ""}
                    left join {self.snap_database}."{table.name}" c on {join_sql_ac} and date(concat(c._year, '-', c._month, '-', c._day)) = date('{{start_date}}')
                where true
                    and a.latest_rownum = 1
            ), snap_diff as (
                select
                    a.*
                from
                    {self.snap_database}."{table.name}" a
                    left join streamwithcreate b on {join_sql_ab}
                where true
                    and date(concat(a._year, '-', a._month, '-', a._day)) = date('{{start_date}}')
                    and b.{table.keys[0]} is null
            ), final as (
                select
                    {col_list_with_subs}
                    {{extra_stream_cols}}
                from
                    streamwithcreate
                union all
                select
                    {col_list_with_subs}
                    {{extra_snap_cols}}
                from
                    snap_diff
            )
            select * from final"""

    @context_decorator
    def get_target_projection_sql(self, table, target):
        """Create the materialized projection sql for the specified target"""
        self.ctx.push()

        if target == 'snapshot':
            return table.projection_sql.format(start_date=self.dh.ymd_prev.iso, end_date=self.dh.ymd_prev.iso, extra_stream_cols='', extra_snap_cols='')
        elif target == 'realtime-view':
            return table.projection_sql.format(start_date=self.dh.ymd.iso, end_date=self.dh.ymd_next.iso, extra_stream_cols=", _year, _month, _day, 'stream' as _source", extra_snap_cols=", _year, _month, _day, 'snap' as _source")

        raise Exception(f'Unsupported target `{target}` encountered')


############################################################################################################################
def lambda_handler(event, context):
    instance = DailySnapshotter()
    return instance.event_handler(event, context)

if __name__ == "__main__":
    print('\n{}\n{}\n'.format('*' * 100, '*** THIS IS ONLY INVOKED IN LOCAL DEV ***'))
    env_type                              = 'd2'
    os.environ['IS_LOCAL']                = 'True'
    os.environ['AWS_DEFAULT_REGION']      = 'us-east-2'
    os.environ['ATHENA_RESULTS_LOCATION'] = f's3://athena-misc-{env_type}/streaming_switch_help'
    os.environ['STREAM_DATABASE']         = f'{env_type}_switch_help_stream'
    os.environ['SNAP_DATABASE']           = f'{env_type}_switch_help_snap'
    os.environ['SNAP_BUCKET']             = f'{env_type}-datalake-layer3-snap-us-east-2'
    os.environ['SNAP_PREFIX']             = 'snap/projection/switch_help'
    os.environ['SNAP_DUMP_FULL_SQL']      = 'True'
    os.environ['SNAP_DRY_RUN']            = 'False'
    os.environ['SNAP_TABLES']             = 'sip_trunk'
    os.environ['SNAP_START_DATE']         = '2023-08-28'
    os.environ['ATHENA_WORK_GROUP']       = 'primary_v3'

    instance = DailySnapshotter()
    instance.event_handler(None, None)

############################################################################################################################
