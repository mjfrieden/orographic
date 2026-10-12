"""Named offline SQL regressions; no data services or full test discovery.

Load only the literal VIEW_SQL definitions, avoiding producer imports and side
effects. Execute the production queries with typed in-memory DuckDB fixtures.
"""
from pathlib import Path
import ast
import tempfile
import unittest
import duckdb

ROOT = Path(__file__).resolve().parents[2]

def view_sql():
    tree = ast.parse((ROOT / 'engine/orographic/shared_mart_consumers.py').read_text())
    assignment = next(n for n in tree.body if isinstance(n, ast.AnnAssign)
                      and isinstance(n.target, ast.Name) and n.target.id == 'VIEW_SQL')
    return ast.literal_eval(assignment.value)

class ExitReplayEntryIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.db = duckdb.connect()
        self.sql = view_sql()
        self.db.execute('''CREATE TABLE recommendations (
          recommendation_key VARCHAR, run_key VARCHAR, source_system VARCHAR,
          cohort VARCHAR, lane VARCHAR, model_version VARCHAR,
          decision_at_utc VARCHAR, underlying_symbol VARCHAR, contract_symbol VARCHAR,
          option_type VARCHAR, expiry_date VARCHAR, entry_bid DOUBLE,
          entry_ask DOUBLE, entry_mid DOUBLE, score DOUBLE, source_bundle_id VARCHAR)''')
        self.db.execute('''CREATE TABLE option_quotes (
          recommendation_key VARCHAR, quote_key VARCHAR, observed_at_utc VARCHAR,
          quote_source VARCHAR, bid DOUBLE, ask DOUBLE, mid DOUBLE,
          executable_exit DOUBLE, open_interest DOUBLE, volume DOUBLE, source_bundle_id VARCHAR)''')
        self.db.execute('''CREATE TABLE execution_outcomes (
          recommendation_key VARCHAR, is_executable BOOLEAN, is_excluded BOOLEAN,
          executable_return DOUBLE)''')
        self.db.execute('CREATE TABLE feature_snapshots (recommendation_key VARCHAR)')
    def tearDown(self):
        self.db.close()
    def add(self, key, bid, ask, mid=1.1):
        self.db.execute("INSERT INTO recommendations VALUES (?, 'run', 'orographic', 'primary', 'research', 'v1', '2026-09-16T20:10:29Z', 'SPY', 'SPY260923C00760000', 'call', '2026-09-23', ?, ?, ?, 0.7, 'fixture')", [key,bid,ask,mid])
        self.db.execute("INSERT INTO option_quotes VALUES (?, ?, '2026-09-17T14:09:51Z', 'trajectory_mark', 1.5, 1.6, 1.55, 1.5, 10, 10, 'fixture')", [key,key+'|q'])
    def rows(self):
        cur=self.db.execute(self.sql['orographic_exit_replay_v1'])
        return [dict(zip([d[0] for d in cur.description],r)) for r in cur.fetchall()]
    def test_crossed_entries_are_excluded_without_changing_evidence(self):
        self.add('bad-1',2.57,2.55,2.56);self.add('bad-2',3.89,3.88,3.885)
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.db.execute('SELECT entry_bid,entry_ask FROM recommendations ORDER BY recommendation_key').fetchall(),[(2.57,2.55),(3.89,3.88)])
        reasons=self.db.execute(self.sql['orographic_execution_quality_v1']).fetchdf()
        self.assertEqual(reasons.exit_replay_entry_eligibility_reason.tolist(), ['crossed_entry_quote']*2)
    def test_invalid_entries_do_not_fall_back_to_mid(self):
        for i,(bid,ask) in enumerate([(1,None),(1,0),(1,-1),(None,1.2),(-1,1.2),(1,float('inf')),(float('inf'),2),(1,float('nan')),(float('nan'),2)]):
            self.add(str(i),bid,ask)
        self.assertEqual(self.rows(), [])
    def test_valid_ask_to_bid_returns_and_labels_are_unchanged(self):
        self.add('normal',1,1.2);self.add('zero-bid',0,1.2);self.add('locked',1.2,1.2)
        self.db.execute("INSERT INTO execution_outcomes VALUES ('normal', true, false, 0.25)")
        result=self.rows();self.assertEqual(len(result),3)
        for row in result:
            self.assertEqual(row['executable_entry'],1.2)
            self.assertAlmostEqual(row['executable_path_return'],0.25)
        self.assertEqual(self.db.execute('SELECT executable_return FROM execution_outcomes').fetchall(),[(0.25,)])
    def test_exclusion_counts_remain_in_bounded_summary(self):
        self.add('crossed',2.57,2.55);self.add('ask',1,None);self.add('bid',None,1.2);self.add('good',1,1.2)
        # Execute the actual new summary query, without running any producers.
        source=(ROOT/'engine/orographic/shared_mart_shadow.py').read_text()
        tree=ast.parse(source)
        query=next(n for n in ast.walk(tree) if isinstance(n,ast.JoinedStr)
                   and any(isinstance(v,ast.Constant) and isinstance(v.value,str)
                           and 'AS crossed_entry_recommendations_with_path_quotes' in v.value for v in n.values))
        with tempfile.TemporaryDirectory() as d:
            execution=Path(d)/'quality.parquet'
            self.db.execute("COPY ("+self.sql['orographic_execution_quality_v1']+") TO '"+str(execution)+"' (FORMAT PARQUET)")
            sql=eval(compile(ast.Expression(query),'<summary>','eval'),{}, {'execution':str(execution)})
            self.assertEqual(self.db.execute(sql).fetchone(),(1,1,1))
        self.assertEqual(len(self.rows()),1)

class ConsumerVersionValidationTests(unittest.TestCase):
    def validator(self):
        # Exercise the actual metadata/hash validator without producer imports.
        import hashlib
        import json
        source=(ROOT/'engine/orographic/shared_mart_consumers.py').read_text()
        tree=ast.parse(source)
        wanted={'CONSUMER_SCHEMA_VERSION','PRODUCTION_AUTHORITY','VIEW_SQL','VIEW_KEYS'}
        selected=[]
        for node in tree.body:
            target=node.target if isinstance(node,ast.AnnAssign) else node.targets[0] if isinstance(node,ast.Assign) else None
            if isinstance(target,ast.Name) and target.id in wanted:
                selected.append(ast.get_source_segment(source,node))
            elif isinstance(node,ast.FunctionDef) and node.name in {'_sha256','validate_shared_mart_consumer_bundle'}:
                selected.append(ast.get_source_segment(source,node))
        env={'Path':Path,'hashlib':hashlib,'json':json}
        exec('from __future__ import annotations\n'+'\n'.join(selected),env)
        return env
    def test_v4_bundle_fails_explicit_schema_validation(self):
        self.check_version('orographic_shared_mart_consumers_v4', False)
    def test_v5_bundle_passes_schema_validation(self):
        self.check_version('orographic_shared_mart_consumers_v5', True)
    def check_version(self, version, accepted):
        import hashlib
        import json
        env=self.validator()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);views={}
            # These are isolated metadata-validator fixtures, not consumer data.
            for name,keys in env['VIEW_KEYS'].items():
                path=root/(name+'.parquet');path.write_bytes(b'synthetic hash fixture')
                views[name]={'path':path.name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'primary_key':list(keys)}
            manifest={'artifact':'orographic_shared_mart_consumer_bundle','schema_version':version,
                      'status':'ready','production_authority':env['PRODUCTION_AUTHORITY'],
                      'source_systems':['cirrus','orographic'],'views':views}
            (root/'consumer_manifest.json').write_text(json.dumps(manifest))
            validate=env['validate_shared_mart_consumer_bundle']
            if accepted:
                self.assertEqual(validate(root)['schema_version'],version)
            else:
                with self.assertRaisesRegex(ValueError,'Shared mart consumer validation failed: schema_version$'):
                    validate(root)

if __name__ == '__main__':
    unittest.main()
