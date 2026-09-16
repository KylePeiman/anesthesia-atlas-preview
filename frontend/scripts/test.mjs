import {createRequire} from 'node:module';
import {mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {spawnSync} from 'node:child_process';

const require=createRequire(import.meta.url);
const {build}=createRequire(require.resolve('vite'))('esbuild');
const directory=await mkdtemp(join(tmpdir(),'atlas-ui-tests-'));
try {
 const outfile=join(directory,'review.test.cjs');
 await build({entryPoints:['tests/review.test.tsx'],bundle:true,platform:'node',format:'cjs',outfile,jsx:'automatic',logLevel:'silent'});
 const result=spawnSync(process.execPath,['--test',outfile],{stdio:'inherit'});
 process.exitCode=result.status??1;
} finally {
 await rm(directory,{recursive:true,force:true});
}
