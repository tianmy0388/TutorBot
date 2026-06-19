# 第 5 章：RNN 与序列建模

## 5.1 序列数据的挑战

CNN 假设输入输出维度固定，但很多任务是**序列**：
- 文本（不定长 token）
- 语音（不定长帧）
- 时间序列（股票、心电图）

序列任务的核心要求：
1. 处理变长输入
2. 记住历史信息
3. 捕获时序依赖

## 5.2 RNN 基本结构

$$\mathbf{h}_t = \sigma(W_{hh} \mathbf{h}_{t-1} + W_{xh} \mathbf{x}_t + \mathbf{b})$$
$$\mathbf{y}_t = W_{hy} \mathbf{h}_t$$

```
    x_1       x_2       x_3       x_4
    ↓         ↓         ↓         ↓
  [h_0] →  [h_1]  →  [h_2]  →  [h_3]  →  [h_4]
    ↓         ↓         ↓         ↓
    y_1       y_2       y_3       y_4
```

## 5.3 BPTT（时间反向传播）

把 RNN 沿时间展开，按反向传播计算梯度。问题：梯度在时间步上连乘 → **梯度消失/爆炸**。

## 5.4 LSTM（Long Short-Term Memory）

核心思想：引入**细胞状态** $C_t$（传送带）+ **三个门控**：

| 门 | 作用 |
|---|---|
| 遗忘门 $f_t$ | 决定丢弃哪些旧信息 |
| 输入门 $i_t$ | 决定写入哪些新信息 |
| 输出门 $o_t$ | 决定输出哪些状态 |

公式：

$$f_t = \sigma(W_f \cdot [h_{t-1}, x_t] + b_f)$$
$$i_t = \sigma(W_i \cdot [h_{t-1}, x_t] + b_i)$$
$$\tilde{C}_t = \tanh(W_C \cdot [h_{t-1}, x_t] + b_C)$$
$$C_t = f_t \odot C_{t-1} + i_t \odot \tilde{C}_t$$
$$o_t = \sigma(W_o \cdot [h_{t-1}, x_t] + b_o)$$
$$h_t = o_t \odot \tanh(C_t)$$

## 5.5 GRU（门控循环单元）

LSTM 的简化版，合并遗忘门与输入门：

$$z_t = \sigma(W_z \cdot [h_{t-1}, x_t])$$
$$r_t = \sigma(W_r \cdot [h_{t-1}, x_t])$$
$$\tilde{h}_t = \tanh(W \cdot [r_t \odot h_{t-1}, x_t])$$
$$h_t = (1 - z_t) \odot h_{t-1} + z_t \odot \tilde{h}_t$$

参数比 LSTM 少 25%，性能相近。

## 5.6 双向 RNN

某些任务需要看完整序列才能判断（如命名实体识别）：

$$\overrightarrow{h}_t = \text{RNN}_f(x_t), \quad \overleftarrow{h}_t = \text{RNN}_b(x_t)$$
$$h_t = [\overrightarrow{h}_t; \overleftarrow{h}_t]$$

## 5.7 Seq2Seq 模型

Encoder-Decoder 架构：

```
Encoder:    x_1 → x_2 → x_3 → x_4 → [context]
                                          ↓
Decoder:                          [start] → y_1 → y_2 → [end]
```

应用：机器翻译、文本摘要、对话生成。

## 5.8 Attention 机制（Seq2Seq 版）

Decoder 每一步对 Encoder 所有隐状态加权：

$$\alpha_{t,s} = \frac{\exp(e_{t,s})}{\sum_{s'} \exp(e_{t,s'})}, \quad c_t = \sum_s \alpha_{t,s} h_s$$

这是 Transformer 的前身。

## 5.9 PyTorch 实现

```python
import torch.nn as nn

class LSTMClassifier(nn.Module):
    def __init__(self, vocab_size, embed_dim, hidden_dim, num_classes):
        super().__init__()
        self.embed = nn.Embedding(vocab_size, embed_dim)
        self.lstm = nn.LSTM(embed_dim, hidden_dim, batch_first=True,
                            bidirectional=True, num_layers=2)
        self.fc = nn.Linear(hidden_dim * 2, num_classes)

    def forward(self, x):
        e = self.embed(x)            # (B, T, E)
        out, (h, c) = self.lstm(e)   # (B, T, 2H)
        # 用最后时刻
        h_fwd = h[-2]
        h_bwd = h[-1]
        return self.fc(torch.cat([h_fwd, h_bwd], dim=1))
```

## 本章小结

- RNN 用循环连接处理序列，但存在梯度消失/爆炸
- LSTM / GRU 通过门控机制缓解了这个问题
- Seq2Seq + Attention 推动了机器翻译等任务
- 缺点：RNN 必须串行计算，难以并行化（Transformer 解决）

## 思考题

1. LSTM 为什么能缓解梯度消失？
2. 双向 RNN 能否用于实时语音识别？为什么？
