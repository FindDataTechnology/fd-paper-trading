import { useState } from "react";
import {
  apiErrorMessage,
} from "../api/client";
import {
  useApiTokens,
  useCreateApiToken,
  useRevokeApiToken,
} from "../api/hooks";

/**
 * API tokens (`fdt_…`) — the long-lived credential an external client uses to
 * reach this service on the user's behalf. The plaintext is shown exactly once,
 * right after minting, because the API never returns it again; the list shows
 * state only.
 *
 * Why this page exists: 壹座's paper-trading card tells the user to "mint a
 * token on the service web and paste it". Before this page that sentence pointed
 * at nothing — `POST /api/v1/tokens` was reachable only by curl.
 */
export default function TokensPage() {
  const { data: tokens, isLoading, error } = useApiTokens();
  const create = useCreateApiToken();
  const revoke = useRevokeApiToken();

  const [name, setName] = useState("壹座");
  const [fresh, setFresh] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [actionError, setActionError] = useState("");

  async function mint() {
    setActionError("");
    setCopied(false);
    try {
      const created = await create.mutateAsync(name.trim() || "default");
      setFresh(created.token);
    } catch (e) {
      setActionError(apiErrorMessage(e));
    }
  }

  async function drop(id: string) {
    setActionError("");
    try {
      await revoke.mutateAsync(id);
    } catch (e) {
      setActionError(apiErrorMessage(e));
    }
  }

  return (
    <div className="mx-auto max-w-3xl p-6">
      <h1 className="text-xl font-semibold">API 令牌</h1>
      <p className="mt-1 text-sm text-muted-foreground">
        给外部客户端用的长命令牌（<code>fdt_…</code>）。铸造后只显示一次，请立即粘贴到
        壹座的「设置 → 连接 → 柏讯·模拟交易」。吊销立即生效。
      </p>

      <section className="mt-6 rounded-md border border-border p-4">
        <label className="block text-sm font-medium" htmlFor="token-name">
          令牌名称（便于日后辨认）
        </label>
        <div className="mt-2 flex gap-2">
          <input
            id="token-name"
            data-testid="token-name-input"
            className="flex-1 rounded border border-border bg-background px-2 py-1 text-sm"
            value={name}
            maxLength={120}
            onChange={(e) => setName(e.target.value)}
          />
          <button
            type="button"
            data-testid="token-mint"
            className="rounded bg-primary px-3 py-1 text-sm text-primary-foreground disabled:opacity-50"
            disabled={create.isPending}
            onClick={mint}
          >
            {create.isPending ? "铸造中…" : "铸造新令牌"}
          </button>
        </div>

        {fresh && (
          <div className="mt-4 rounded border border-border bg-muted/30 p-3">
            <p className="text-xs text-muted-foreground">
              这是该令牌的唯一一次明文显示，离开本页后无法再取回：
            </p>
            <div className="mt-2 flex items-center gap-2">
              <code
                data-testid="token-plaintext"
                className="flex-1 break-all font-mono text-xs"
              >
                {fresh}
              </code>
              <button
                type="button"
                data-testid="token-copy"
                className="shrink-0 rounded border border-border px-2 py-1 text-xs"
                onClick={async () => {
                  try {
                    await navigator.clipboard.writeText(fresh);
                    setCopied(true);
                  } catch {
                    setCopied(false);
                  }
                }}
              >
                {copied ? "已复制" : "复制"}
              </button>
            </div>
            <p className="mt-2 text-xs text-muted-foreground">
              把它粘贴到壹座 →「设置 → 连接」→「柏讯·模拟交易」。
            </p>
          </div>
        )}

        {actionError && (
          <p data-testid="token-error" className="mt-3 text-sm text-destructive">
            {actionError}
          </p>
        )}
      </section>

      <section className="mt-6">
        <h2 className="text-sm font-medium">已签发的令牌</h2>
        {isLoading && <p className="mt-2 text-sm text-muted-foreground">加载中…</p>}
        {error && (
          <p className="mt-2 text-sm text-destructive">{apiErrorMessage(error)}</p>
        )}
        {tokens && tokens.length === 0 && (
          <p data-testid="token-empty" className="mt-2 text-sm text-muted-foreground">
            还没有令牌。
          </p>
        )}
        {tokens && tokens.length > 0 && (
          <table data-testid="token-table" className="mt-2 w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-muted-foreground">
                <th className="py-1">名称</th>
                <th>签发时间</th>
                <th>最近使用</th>
                <th>状态</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {tokens.map((t) => (
                <tr key={t.id} data-testid="token-row" className="border-t border-border">
                  <td className="py-1">{t.name}</td>
                  <td className="text-xs">{new Date(t.created_at).toLocaleString()}</td>
                  <td className="text-xs">
                    {t.last_used_at ? new Date(t.last_used_at).toLocaleString() : "未使用"}
                  </td>
                  <td className="text-xs">
                    {t.revoked_at ? "已吊销" : <span className="text-green-600">有效</span>}
                  </td>
                  <td className="text-right">
                    {!t.revoked_at && (
                      <button
                        type="button"
                        data-testid="token-revoke"
                        className="text-xs text-destructive underline"
                        disabled={revoke.isPending}
                        onClick={() => drop(t.id)}
                      >
                        吊销
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <p className="mt-3 text-xs text-muted-foreground">
          明文不会出现在这里——列表只显示状态。令牌若被拒绝，重新铸造一枚即可。
        </p>
      </section>
    </div>
  );
}
