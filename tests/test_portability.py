import copy
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import unquote,urlsplit
from s5validation.bootstrap import initialize
from s5validation.credentials import read_credentials, mc_environment, credential_path, downloader_credentials
from s5validation.downloads import McProduction
from s5validation.cli import main

BASE=Path(__file__).resolve().parents[1]

class CredentialTests(unittest.TestCase):
    def file(self,root,secret='fixture secret#/?@'):
        path=root/'private.env'
        path.write_text('IRIDE_S3_ENDPOINT="https://storage.example.test"\nIRIDE_PROD_ACCESS_KEY="fixture-access"\nIRIDE_PROD_SECRET_KEY="'+secret+'"\n', encoding='utf-8')
        return path

    def test_literal_credentials_and_encoding(self):
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{},clear=True):
            path=self.file(Path(temp));env,_=mc_environment('iride-prod',path)
            connection=urlsplit(env['MC_HOST_iride-prod'])
            self.assertEqual(unquote(connection.password),'fixture secret#/?@')
            self.assertEqual(connection.hostname,'storage.example.test')
            self.assertNotIn('IRIDE_PROD_SECRET_KEY',env)
            literal=self.file(Path(temp),'$(touch NEVER_EXECUTE)')
            self.assertEqual(read_credentials(literal)['IRIDE_PROD_SECRET_KEY'],'$(touch NEVER_EXECUTE)')

    def test_missing_placeholder_and_invalid_endpoint(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);path=self.file(root,'REPLACE_SECRET')
            with self.assertRaises(ValueError): read_credentials(path)
            path=self.file(root);path.write_text(path.read_text(encoding='utf-8').replace('https://storage.example.test','http://storage.example.test'), encoding='utf-8')
            with self.assertRaises(ValueError): read_credentials(path)

    def test_minio_error_redacts_credentials(self):
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{},clear=True):
            path=self.file(Path(temp))
            result=subprocess.CompletedProcess(['mc'],1,stdout='',stderr='fixture-access fixture secret#/?@ failed')
            with patch('s5validation.downloads.shutil.which',return_value='mc'),patch('s5validation.downloads.subprocess.run',return_value=result) as called:
                client=McProduction(credentials_file=path)
                with self.assertRaises(OSError) as caught: client.command('ls','--json','iride-prod/bucket/')
                self.assertNotIn('fixture-access',str(caught.exception));self.assertNotIn('fixture secret',str(caught.exception))
                self.assertNotIn('fixture-access',' '.join(called.call_args.args[0]))
                self.assertIn('MC_HOST_iride-prod',called.call_args.kwargs['env'])

    def test_repository_private_file_discovery(self):
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{},clear=True):
            root=Path(temp);(root/'credentials').mkdir();path=root/'credentials/bucket.env';path.write_text('fixture', encoding='utf-8')
            self.assertEqual(credential_path(base=root),path)
            os.environ['S5_VALIDATION_CREDENTIALS']=str(root/'override.env')
            self.assertEqual(credential_path(base=root),root/'override.env')

    def test_optional_downloader_file_and_alias(self):
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{},clear=True):
            root=Path(temp);(root/'credentials').mkdir();path=root/'credentials/downloaders.env'
            path.write_text('PLANETARY_COMPUTER_SUBSCRIPTION_KEY=""\nEARTHDATA_USER="fixture-user"\nCDS_KEY="REPLACE_KEY"\n', encoding='utf-8')
            values=downloader_credentials({},root)
            self.assertEqual(values['EARTHDATA_USERNAME'],'fixture-user')
            self.assertNotIn('PLANETARY_COMPUTER_SUBSCRIPTION_KEY',values);self.assertNotIn('CDS_KEY',values)
            self.assertEqual(downloader_credentials({'references':{'credentials_file':str(path)}},root),values)

class BootstrapTests(unittest.TestCase):
    def fixture(self,root):
        cfg=copy.deepcopy(json.loads((BASE/'config/validation.json').read_text(encoding='utf-8')));geometry=root/'aoi.geojson'
        geometry.write_text(json.dumps({'type':'Polygon','coordinates':[[[10,35],[20,35],[20,50],[10,50],[10,35]]]}), encoding='utf-8')
        cfg['campaign']['aoi_file']='aoi.geojson';cfg['landsat']['land_aoi_file']='aoi.geojson';cfg['landsat']['marine_aoi_file']='aoi.geojson'
        return cfg

    def test_every_run_creates_and_preserves_state(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);cfg=self.fixture(root);work=root/'validation';initialize(cfg,root,work)
            self.assertTrue((root/'credentials').is_dir());self.assertEqual(json.loads((work/'deliveries.json').read_text(encoding='utf-8')),[])
            manifest=work/'deliveries.json';manifest.write_text('[{"fixture":"preserved"}]', encoding='utf-8');initialize(cfg,root,work)
            self.assertEqual(json.loads(manifest.read_text(encoding='utf-8'))[0]['fixture'],'preserved')
            for folder in ('cache/s5','cache/reference','results/S5-02-11','report','inventory'): self.assertTrue((work/folder).is_dir())

    def test_missing_aoi_and_invalid_campaign_fail(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);cfg=self.fixture(root);cfg['campaign']['end']=cfg['campaign']['start']
            with self.assertRaises(ValueError): initialize(cfg,root,root/'validation')
            cfg=self.fixture(root);(root/'aoi.geojson').unlink()
            with self.assertRaises(ValueError): initialize(cfg,root,root/'validation')

    def test_resource_checksum_guard(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);cfg=self.fixture(root);(root/'aoi').mkdir()
            (root/'aoi/manifest.json').write_text(json.dumps({'files':{'aoi.geojson':{'sha256':'not-the-current-hash'}}}), encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'checksum'): initialize(cfg,root,root/'validation')

    def test_cli_custom_workdir_gets_separate_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);cfg=self.fixture(root);(root/'config').mkdir();path=root/'config/validation.json';path.write_text(json.dumps(cfg), encoding='utf-8');work=root/'separate'
            self.assertEqual(main(['--config',str(path),'--workdir',str(work),'--status']),0)
            self.assertTrue((work/'deliveries.json').exists());self.assertFalse((root/'validation/deliveries.json').exists())
