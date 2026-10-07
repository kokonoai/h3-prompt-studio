import React from 'react';
import {createRoot} from 'react-dom/client';
import App from './App';
import {UiLanguageProvider} from './i18n';
import './style.css';

type ErrorBoundaryState={error:Error|null};
class AppErrorBoundary extends React.Component<React.PropsWithChildren,ErrorBoundaryState>{
  state:ErrorBoundaryState={error:null};
  static getDerivedStateFromError(error:Error){return {error};}
  componentDidCatch(error:Error,info:React.ErrorInfo){console.error('[H3 UI recovered from a render error]',error,info);}
  render(){
    if(!this.state.error)return this.props.children;
    return <main className="app-crash-recovery"><section><h1>页面显示发生错误</h1><p>The Studio page hit a display error. Background generation and saved files were not deleted.</p><code>{this.state.error.message}</code><button onClick={()=>location.reload()}>重新载入页面 · Reload</button></section></main>;
  }
}

createRoot(document.getElementById('root')!).render(<AppErrorBoundary><UiLanguageProvider><App /></UiLanguageProvider></AppErrorBoundary>);
