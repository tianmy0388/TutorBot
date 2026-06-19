# 第 6 章：Transformer 与注意力机制

## 6.1 注意力机制的直觉

人类阅读时会"关注"句子中相关部分。Attention 让模型学习**该关注什么**。

## 6.2 Self-Attention

Query、Key、Value 三元组：

$$\text{Attention}(Q, K, V) = \text{softmax}\left(\frac{QK^\top}{\sqrt{d_k}}\right) V$$

- $Q$：查询向量（"我想找什么"）
- $K$：键向量（"我是什么"）
- $V$：值向量（"我能提供什么信息"）
- $\sqrt{d_k}$：缩放因子，防止点积过大导致 softmax 饱和

直观理解：
- $QK^\top$ 计算每个位置与其他位置的相似度
- softmax 归一化为概率分布（注意力权重）
- 加权求和 $V$ 得到每个位置的新表示

## 6.3 Multi-Head Attention

并行多个独立的注意力头，捕获不同子空间的依赖：

$$\text{MultiHead}(Q, K, V) = \text{Concat}(\text{head}_1, \ldots, \text{head}_h) W^O$$

$$\text{head}_i = \text{Attention}(QW_i^Q, KW_i^K, VW_i^V)$$

不同头可以学习：句法关系、指代关系、长程依赖等不同模式。

## 6.4 Positional Encoding

Self-Attention 本身是置换不变的（打乱输入顺序结果一样），但语言有顺序。需要加入位置信息：

$$PE_{(pos, 2i)} = \sin(pos / 10000^{2i/d})$$
$$PE_{(pos, 2i+1)} = \cos(pos / 10000^{2i/d})$$

或可学习的 positional embedding。

## 6.5 Transformer Block

每个 Block 包含两个子层，均使用残差连接 + LayerNorm：

```
x → LayerNorm → Multi-Head Attention → Add(x, ·) 
  → LayerNorm → Feed-Forward → Add(·, ·) → out
```

Feed-Forward Network：
$$\text{FFN}(x) = \max(0, xW_1 + b_1) W_2 + b_2$$

## 6.6 完整 Transformer 架构

### Encoder（编码器）
- N 个相同 Block 堆叠
- 每个 Block：Self-Attention + FFN

### Decoder（解码器）
- N 个相同 Block 堆叠
- 每个 Block：**Masked** Self-Attention + Cross-Attention + FFN
- Masked Self-Attention 保证生成时不看到未来位置

## 6.7 三种 Attention 变体

| 类型 | Q 来源 | K, V 来源 | 用途 |
|---|---|---|---|
| Self-Attention | 同一序列 | 同一序列 | 编码器内 |
| Masked Self-Attention | 解码器当前位置 | 解码器历史位置 | 自回归生成 |
| Cross-Attention | 解码器 | 编码器输出 | Seq2Seq |

## 6.8 Transformer vs RNN

| 维度 | RNN/LSTM | Transformer |
|---|---|---|
| 并行计算 | ✗ 必须串行 | ✓ 完全并行 |
| 长程依赖 | 困难 | 容易（任意距离 O(1) 路径） |
| 复杂度 | $O(n)$ 串行 | $O(n^2)$ 注意力，但可并行 |
| 显存 | 较低 | 较高（n×n 注意力矩阵） |

## 6.9 高效 Transformer（解决 $O(n^2)$ 问题）

- **Sparse Attention**（Longformer, BigBird）：只计算部分位置对
- **Linear Attention**（Performer, Linformer）：用核函数近似
- **Flash Attention**（Tri Dao）：IO-aware 精确实现
- **Sliding Window + Global**（Mistral）：混合稀疏

## 6.10 PyTorch 实现（简化版）

```python
import torch
import torch.nn as nn
import math

class MultiHeadAttention(nn.Module):
    def __init__(self, d_model, n_heads):
        super().__init__()
        assert d_model % n_heads == 0
        self.d_model = d_model
        self.n_heads = n_heads
        self.d_k = d_model // n_heads
        self.W_q = nn.Linear(d_model, d_model)
        self.W_k = nn.Linear(d_model, d_model)
        self.W_v = nn.Linear(d_model, d_model)
        self.W_o = nn.Linear(d_model, d_model)

    def forward(self, x, mask=None):
        B, T, _ = x.shape
        q = self.W_q(x).view(B, T, self.n_heads, self.d_k).transpose(1, 2)
        k = self.W_k(x).view(B, T, self.n_heads, self.d_k).transpose(1, 2)
        v = self.W_v(x).view(B, T, self.n_heads, self.d_k).transpose(1, 2)

        scores = (q @ k.transpose(-2, -1)) / math.sqrt(self.d_k)
        if mask is not None:
            scores = scores.masked_fill(mask == 0, -1e9)
        attn = scores.softmax(dim=-1)
        out = (attn @ v).transpose(1, 2).contiguous().view(B, T, self.d_model)
        return self.W_o(out)
```

## 本章小结

- Self-Attention 是 Transformer 的核心
- Multi-Head 让不同头关注不同模式
- Positional Encoding 注入顺序信息
- 完全并行 → 训练效率极高 → 模型可以做得很大 → LLM 诞生

## 思考题

1. 为什么 Self-Attention 要除以 $\sqrt{d_k}$？
2. Multi-Head Attention 比单头好在哪儿？
