import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { PropsWithChildren } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { aibrainKeys } from "@/lib/aibrain/keys";
import type {
  ConversationDetail,
  SendMessageRequest,
  SendMessageResponse
} from "@/lib/aibrain/types";

const api = vi.hoisted(() => ({
  createConversation: vi.fn(),
  sendMessage: vi.fn()
}));

vi.mock("@/lib/aibrain/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/aibrain/api")>();
  return {
    ...actual,
    createConversation: api.createConversation,
    sendMessage: api.sendMessage
  };
});

import { useCreateConversation, useSendMessage } from "@/lib/aibrain/hooks";

const CONVERSATION: ConversationDetail = {
  id: "conv-1",
  title: "新会话",
  created_at: "2026-08-30T10:00:00Z",
  updated_at: "2026-08-30T10:00:00Z",
  messages: []
};

const CONVERSATION_SUMMARY = {
  id: CONVERSATION.id,
  title: CONVERSATION.title,
  created_at: CONVERSATION.created_at,
  updated_at: CONVERSATION.updated_at
};

const SEND_BODY: SendMessageRequest = {
  content: "你好",
  tier: "high",
  attachment_asset_ids: []
};

const SEND_RESPONSE: SendMessageResponse = {
  user_message: {
    id: "message-user-1",
    conversation_id: CONVERSATION.id,
    role: "user",
    content: SEND_BODY.content,
    attachments: [],
    tier: SEND_BODY.tier,
    status: "completed",
    created_at: "2026-08-30T10:00:01Z"
  },
  assistant_message: {
    id: "message-assistant-1",
    conversation_id: CONVERSATION.id,
    role: "assistant",
    content: "（高档 · gpt-5.6-sol）已收到",
    attachments: [],
    tier: SEND_BODY.tier,
    model: "gpt-5.6-sol",
    status: "completed",
    created_at: "2026-08-30T10:00:02Z"
  },
  wallet: {
    available_credits: 480,
    reserved_credits: 0,
    total_topup_credits: 500,
    total_spent_credits: 20,
    topup_options: [100, 500, 1000, 2000],
    single_request_limit: 200
  },
  cooldown_retry_after_seconds: null
};

function makeClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: 30_000 },
      mutations: { retry: false }
    }
  });
}

function wrapper(client: QueryClient) {
  return function Wrapper({ children }: PropsWithChildren) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("AIBrain 会话详情缓存", () => {
  it("创建会话成功后立即缓存服务端返回的空详情", async () => {
    const client = makeClient();
    api.createConversation.mockResolvedValue(CONVERSATION);
    const { result } = renderHook(() => useCreateConversation(), { wrapper: wrapper(client) });

    await act(async () => {
      await result.current.mutateAsync();
    });

    expect(client.getQueryData(aibrainKeys.conversation(CONVERSATION.id))).toEqual(CONVERSATION);
  });

  it("发送成功响应在旧的空详情请求结束后仍是当前会话的权威消息", async () => {
    const client = makeClient();
    const detailKey = aibrainKeys.conversation(CONVERSATION.id);
    const oldDetail = deferred<ConversationDetail>();
    const oldDetailStarted = vi.fn();

    client.setQueryData(detailKey, CONVERSATION);
    await client.invalidateQueries({ queryKey: detailKey, refetchType: "none" });
    const staleRequest = client.prefetchQuery({
      queryKey: detailKey,
      queryFn: () => {
        oldDetailStarted();
        return oldDetail.promise;
      }
    });
    await waitFor(() => expect(oldDetailStarted).toHaveBeenCalledOnce());

    api.sendMessage.mockResolvedValue(SEND_RESPONSE);
    const { result } = renderHook(() => useSendMessage(), { wrapper: wrapper(client) });
    await act(async () => {
      await result.current.mutateAsync({ conversationId: CONVERSATION.id, body: SEND_BODY });
    });

    oldDetail.resolve({ ...CONVERSATION, messages: [] });
    await Promise.all([oldDetail.promise, staleRequest]);

    expect(client.getQueryData<ConversationDetail>(detailKey)?.messages).toEqual([
      SEND_RESPONSE.user_message,
      SEND_RESPONSE.assistant_message
    ]);
  });

  it("已有会话详情尚未缓存时用列表摘要立即展示发送成功响应", async () => {
    const client = makeClient();
    const detailKey = aibrainKeys.conversation(CONVERSATION.id);
    const oldDetail = deferred<ConversationDetail>();
    const postCommitDetail = deferred<ConversationDetail>();
    const historicalMessage = {
      ...SEND_RESPONSE.assistant_message,
      id: "message-assistant-history",
      content: "历史消息",
      created_at: "2026-08-30T09:59:00Z"
    };
    const detailQuery = vi
      .fn<() => Promise<ConversationDetail>>()
      .mockImplementationOnce(() => oldDetail.promise)
      .mockImplementationOnce(() => postCommitDetail.promise);

    client.setQueryData(aibrainKeys.conversations(), [CONVERSATION_SUMMARY]);
    api.sendMessage.mockResolvedValue(SEND_RESPONSE);
    const { result } = renderHook(
      () => ({
        detail: useQuery({ queryKey: detailKey, queryFn: detailQuery }),
        send: useSendMessage()
      }),
      { wrapper: wrapper(client) }
    );
    await waitFor(() => expect(detailQuery).toHaveBeenCalledOnce());

    await act(async () => {
      await result.current.send.mutateAsync({ conversationId: CONVERSATION.id, body: SEND_BODY });
    });

    expect(client.getQueryData<ConversationDetail>(detailKey)?.messages).toEqual([
      SEND_RESPONSE.user_message,
      SEND_RESPONSE.assistant_message
    ]);
    expect(detailQuery).toHaveBeenCalledTimes(2);

    postCommitDetail.resolve({
      ...CONVERSATION,
      messages: [historicalMessage, SEND_RESPONSE.user_message, SEND_RESPONSE.assistant_message]
    });
    await waitFor(() =>
      expect(client.getQueryData<ConversationDetail>(detailKey)?.messages).toEqual([
        historicalMessage,
        SEND_RESPONSE.user_message,
        SEND_RESPONSE.assistant_message
      ])
    );

    oldDetail.resolve({ ...CONVERSATION, messages: [historicalMessage] });
    await oldDetail.promise;
    expect(client.getQueryData<ConversationDetail>(detailKey)?.messages).toEqual([
      historicalMessage,
      SEND_RESPONSE.user_message,
      SEND_RESPONSE.assistant_message
    ]);
  });

  it("详情和列表摘要都不存在时发送成功会重新读取提交后的完整详情", async () => {
    const client = makeClient();
    const detailKey = aibrainKeys.conversation(CONVERSATION.id);
    const oldDetail = deferred<ConversationDetail>();
    const detailQuery = vi
      .fn<() => Promise<ConversationDetail>>()
      .mockImplementationOnce(() => oldDetail.promise)
      .mockResolvedValueOnce({
        ...CONVERSATION,
        messages: [SEND_RESPONSE.user_message, SEND_RESPONSE.assistant_message]
      });

    api.sendMessage.mockResolvedValue(SEND_RESPONSE);
    const { result } = renderHook(
      () => ({
        detail: useQuery({ queryKey: detailKey, queryFn: detailQuery }),
        send: useSendMessage()
      }),
      { wrapper: wrapper(client) }
    );
    await waitFor(() => expect(detailQuery).toHaveBeenCalledOnce());

    await act(async () => {
      await result.current.send.mutateAsync({ conversationId: CONVERSATION.id, body: SEND_BODY });
    });

    expect(detailQuery).toHaveBeenCalledTimes(2);
    await waitFor(() =>
      expect(result.current.detail.data?.messages).toEqual([
        SEND_RESPONSE.user_message,
        SEND_RESPONSE.assistant_message
      ])
    );

    oldDetail.resolve({ ...CONVERSATION, messages: [] });
    await oldDetail.promise;
    expect(client.getQueryData<ConversationDetail>(detailKey)?.messages).toEqual([
      SEND_RESPONSE.user_message,
      SEND_RESPONSE.assistant_message
    ]);
  });

  it("替换已存在的响应消息时保持后续无关消息的原顺序", async () => {
    const client = makeClient();
    const detailKey = aibrainKeys.conversation(CONVERSATION.id);
    const laterMessage = {
      ...SEND_RESPONSE.assistant_message,
      id: "message-assistant-later",
      content: "后续消息",
      created_at: "2026-08-30T10:00:03Z"
    };
    client.setQueryData<ConversationDetail>(detailKey, {
      ...CONVERSATION,
      updated_at: laterMessage.created_at,
      messages: [
        { ...SEND_RESPONSE.user_message, content: "缓存中的旧用户消息" },
        { ...SEND_RESPONSE.assistant_message, content: "缓存中的旧助手消息" },
        laterMessage
      ]
    });
    api.sendMessage.mockResolvedValue(SEND_RESPONSE);
    const { result } = renderHook(() => useSendMessage(), { wrapper: wrapper(client) });

    await act(async () => {
      await result.current.mutateAsync({ conversationId: CONVERSATION.id, body: SEND_BODY });
    });

    expect(client.getQueryData<ConversationDetail>(detailKey)?.messages).toEqual([
      SEND_RESPONSE.user_message,
      SEND_RESPONSE.assistant_message,
      laterMessage
    ]);
    expect(client.getQueryData<ConversationDetail>(detailKey)?.updated_at).toBe(laterMessage.created_at);
  });
});
