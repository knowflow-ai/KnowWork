/**
 * 云端对话里的文件（方案 7.7.3：网页与桌面同一套）：业务服务按路径读——交付过的读收好的那份，没交付过的从会话沙箱现读；
 * Word、PPT 由服务端转 PDF；存到本机是浏览器下载。本机会话的同一组动作由桌面端扩展给（chat.sources）。
 */
import { conversationFileUrl } from '../../api/conversations';
import { requestBytes, requestFile, saveFile } from '../../api/http';
import { fileNameOf } from './components/fileLinks';
import type { ConversationFiles } from './sources';

export const SERVER_FILES: ConversationFiles = {
  read: (conversationId, path) => requestBytes(conversationFileUrl(conversationId, path)),
  pdf: (conversationId, path) => requestBytes(conversationFileUrl(conversationId, path, 'preview')),
  save: async (conversationId, path) => {
    const file = await requestFile(conversationFileUrl(conversationId, path));
    saveFile(file.blob, file.filename ?? fileNameOf(path));
  },
};
