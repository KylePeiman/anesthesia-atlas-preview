import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig(({mode}) => ({base:process.env.VITE_STATIC_DEMO === 'true' ? './' : '/',plugins:[react()],server:{proxy:{'/api':'http://127.0.0.1:8000'}},define:{__ATLAS_BUILD_MODE__:JSON.stringify(mode)}}));
