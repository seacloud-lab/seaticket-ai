import json
import logging
from boto3 import Session as BotoSession
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

KEY_PREFIX_FORMAT = "{project_uuid}/{site_id}/"

class S3Storage:
    def __init__(self, host, key_id, secret_key, bucket_name):
        self.host = host
        self.key_id = key_id
        self.secret_key = secret_key
        self.bucket = bucket_name

    def init_client(self):
        session = BotoSession()
        self.client = session.client(
            's3',
            aws_access_key_id=self.key_id,
            aws_secret_access_key=self.secret_key,
            endpoint_url=self.host,
            config=BotoConfig(signature_version='s3v4', s3={'addressing_style': 'path'}),
        )
    
    def __enter__(self, *args, **kwargs):
        self.init_client()
        self.client.head_bucket(Bucket=self.bucket)
        return self
    
    def __exit__(self, *args, **kwargs):
        self.client.close()

    @staticmethod
    def _key(project_uuid, site_id, filename):
        return KEY_PREFIX_FORMAT.format(project_uuid=project_uuid, site_id=site_id) + filename
    
    def list_files(self, site_id, project_uuid):
        return list(self.iter_files(site_id, project_uuid))

    def iter_files(self, site_id, project_uuid):
        prefix = KEY_PREFIX_FORMAT.format(project_uuid=project_uuid, site_id=site_id)
        continuation_token = None
        while True:
            params = {'Bucket': self.bucket, 'Prefix': prefix}
            if continuation_token:
                params['ContinuationToken'] = continuation_token
            resp = self.client.list_objects_v2(**params)
            for obj in resp.get('Contents', []) or []:
                key = obj['Key']
                if not key.endswith('.json'):
                    continue
                yield key[len(prefix):]
            if resp.get('IsTruncated'):
                continuation_token = resp.get('NextContinuationToken')
            else:
                break

    def read_json(self, site_id, project_uuid, filename):
        key = self._key(project_uuid, site_id, filename)
        try:
            resp = self.client.get_object(Bucket=self.bucket, Key=key)
            content = resp['Body'].read()
            return json.loads(content)
        except ClientError as e:
            if e.response['Error']['Code'] in ('NoSuchKey', '404'):
                logger.warning(f'read_json error, file not found: {key}')
                return None
            logger.exception('read_json error: %s', e)
            raise e

        except Exception as e:
            logger.exception('read_json error: %s', e)
            return None
