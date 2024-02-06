import os
import sys
import base64
import json
import importlib
from os.path import realpath, dirname, join, basename, exists, getsize

from streaming_lib.utils.context_printer import context_decorator
from streaming_lib.utils.structural import Struct
from streaming_lib.utils.object_encoder import ObjectEncoder
from streaming_lib.utils.declarative_object_mapper import DeclarativeObjectMapper
from streaming_lib.lambdas.base_transformer import EntityTransformFailure, BaseTransformer

class DeclarativeTransformer(BaseTransformer):
    def __init__(self, context, entity=None, mapping=None, verbose=False, trace=False):
        super().__init__(context)
        self.message_loads_keys = []
        self.entity  = (entity or os.environ.get('TX_ENTITY')).replace('-','_')
        self.mapping = mapping or os.environ.get('TX_MAPPING')
        self.verbose = verbose
        self.trace   = trace

    @context_decorator
    def decode_and_parse(self, record):
        mi = super().decode_and_parse(record)
        model_cls = getattr(importlib.import_module(f'models.{self.entity}.model', 'ModelCodegen'))
        self.dom = DeclarativeObjectMapper(model_cls, self.mapping, mi.payload, self.dh, self.verbose, self.trace).parse_message()
        return mi

    @context_decorator
    def _lookup_key(self, key, message_info):
        self.ctx.push()
        lkp = self.dom.dat.lkp_

        if key == 'event_ts':
            return lkp.event.ts
        elif key == 'sent_ts':
            return lkp.sent.ts
        elif key == 'action':
            return lkp.action
        elif key == 'action_group':
            return 'snapshot' if lkp.action.upper() == 'SNAPSHOT' else 'origin'
        elif key == '_keys':
            return lkp.pkey
        else:
            raise Exception(f'Lookup with key="{key}" unsupported for {basename(__file__)}')

    @context_decorator
    def transform_record(self, record_id, message_info, time_table, time_table_extra, enrichment):
        self.ctx.push(mod=self.rec_index)

        mout, dat = self.dom.apply_map(empty_to_none=True)
        return self._map_firehose_response(record_id, dat.lkp_, mout.model_dump())

    def _map_firehose_response(self, record_id, lkp, new_payload):
        partition_keys = {
            'year':  lkp.event.dt.strftime('%Y'),
            'month': lkp.event.dt.strftime('%m'),
            'day':   lkp.event.dt.strftime('%d'),
        }

        retval = {
            'recordId': record_id,
            'result': 'Ok',
            'data': base64.b64encode(json.dumps(new_payload, cls=ObjectEncoder).encode('utf-8')),
            'metadata': {'partitionKeys': partition_keys},
        }

        if self.verbose:
            self.ctx.hbreak(lfs=1)
            self.ctx.printobj(new_payload, 'new_payload')
            self.ctx.printobj(partition_keys, 'partition_keys')

        return retval


############################################################################################################################
def lambda_handler(event, context):
    instance = DeclarativeTransformer(context)
    return instance.event_handler(event, context)

def test_main(files: dict=None):
    from uuid import uuid4
    from os.path import dirname, join
    # print('\n{}\n{}\n'.format('*'*150, '*** THIS IS ONLY INVOKED IN LOCAL DEV ***'))

    os.environ['IS_LOCAL']                = 'True'
    os.environ['TX_ENTITY']               = 'sip-trunk'
    os.environ['TX_MAPPING']              = '{}' ### TODO: Populate this

    context = Struct.make({'aws_request_id': '9e7840f7-3750-4623-ad69-16718bf6f5c2'})
    test_messages_path = join(dirname(__file__), '..', 'tests/sample_messages/sip_trunk/input')
    instance = DeclarativeTransformer(context)

    files = files or {}
    files = dict(
        envelope    = files.get('envelope', 'step2_loaded_envelope-TEMPLATE.json'),
        contents    = files.get('contents', 'step3_envelope_contents-UPDATE1.json'),
        actingparty = files.get('actingparty', 'step3b_envelope_extensions_actingParty1.json'),
    )

    print(files)
    with open(f"{test_messages_path}/step0_raw_firehose_records-TEMPLATE.json") as fp0:
        records_event = json.loads(fp0.read())
        with open(f"{test_messages_path}/step1_decoded_sns_payload-TEMPLATE.json") as fp1:
            sns_payload = json.loads(fp1.read())
            with open(f"{test_messages_path}/{files['envelope']}") as fp2:
                envelope = json.loads(fp2.read())
                with open(f"{test_messages_path}/{files['contents']}") as fp3:
                    entity_message = fp3.read()
                    with open(f"{test_messages_path}/{files['actingparty']}") as fp4:
                        acting_party = fp4.read()
                        envelope['contents'] = entity_message
                        envelope['extensions']['actingParty'] = acting_party
                        sns_payload['Message'] = json.dumps(envelope)
                        rec_data = base64.b64encode(json.dumps(sns_payload).encode('utf-8')).decode('utf-8')
                        records_event['records'][0]['data'] = rec_data
                        records_event['invocationId'] = uuid4()
                        transformed = instance.event_handler(records_event, None)

    return transformed

if __name__ == "__main__":
    import sys
    ## sys.argv[1:]
    test_main()

############################################################################################################################
