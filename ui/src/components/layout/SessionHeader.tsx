import React from 'react';
import type { Session } from '../../types';

export const SessionHeader: React.FC<{
  session: Session;
}> = ({ session }) => {
  // Calculate totals from exchanges
  const totalPromptTokens = React.useMemo(() => {
    return session.exchanges.reduce((sum: number, ex) => sum + (ex.promptTokenCount || 0), 0);
  }, [session.exchanges]);

  const totalGeneratedTokens = React.useMemo(() => {
    return session.exchanges.reduce((sum: number, ex) => {
      if (ex.usage && ex.usage.output_tokens !== undefined) {
        return sum + ex.usage.output_tokens;
      }
      return sum;
    }, 0);
  }, [session.exchanges]);

  const model = session.exchanges.length > 0 ? session.exchanges[0].model : 'N/A';
  const requestCount = session.exchanges.length;
  const messageCount = totalPromptTokens; // In our model, prompt tokens = message count

  return (
    <div className="border-b border-gray-200 dark:border-slate-800 bg-white dark:bg-[#0f172a] px-4 py-3">
      <div className="flex flex-wrap items-center gap-4 text-sm">
        {/* Model */}
        <div className="flex items-center gap-2">
          <span className="font-semibold text-slate-500 dark:text-slate-400">Model:</span>
          <span className="font-mono text-slate-700 dark:text-slate-200">{model}</span>
        </div>

        {/* Requests */}
        <div className="flex items-center gap-2">
          <span className="font-semibold text-slate-500 dark:text-slate-400">Requests:</span>
          <span className="font-mono text-slate-700 dark:text-slate-200">{requestCount}</span>
        </div>

        {/* Messages */}
        <div className="flex items-center gap-2">
          <span className="font-semibold text-slate-500 dark:text-slate-400">Messages:</span>
          <span className="font-mono text-slate-700 dark:text-slate-200">{messageCount}</span>
        </div>

        {/* Prompt Tokens */}
        <div className="flex items-center gap-2">
          <span className="font-semibold text-slate-500 dark:text-slate-400">Prompt:</span>
          <span className="font-mono text-blue-600 dark:text-blue-400">
            {totalPromptTokens.toLocaleString()}
          </span>
        </div>

        {/* Generated Tokens */}
        <div className="flex items-center gap-2">
          <span className="font-semibold text-slate-500 dark:text-slate-400">Generated:</span>
          <span className="font-mono text-purple-600 dark:text-purple-400">
            {totalGeneratedTokens.toLocaleString()}
          </span>
        </div>
      </div>
    </div>
  );
};
