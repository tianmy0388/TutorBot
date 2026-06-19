# 第 4 章：CNN 与计算机视觉

## 4.1 为什么需要 CNN

全连接神经网络处理图像时参数量爆炸：
- 224×224×3 图像输入 → 第一层 1000 个神经元 = 1.5 亿参数

CNN 利用了图像的两个先验：
- **局部性（Locality）**：像素相关性随距离衰减
- **平移不变性（Translation Invariance）**：猫在左边还是右边都是猫

## 4.2 卷积运算

二维卷积：

$$(I * K)[i, j] = \sum_m \sum_n I[i+m, j+n] K[m, n]$$

- **Kernel（卷积核）**：可学习的小矩阵
- **Stride（步长）**：控制输出尺寸
- **Padding（填充）**：保持边界信息
- **多通道**：每个卷积核有 C_in 个通道

## 4.3 关键概念

### 4.3.1 感受野（Receptive Field）

输出一个像素"看到"输入区域的大小。深层神经元感受野更大。

### 4.3.2 池化（Pooling）

- 最大池化（Max Pooling）：取窗口最大值
- 平均池化（Average Pooling）：取平均值
- 作用：降采样、增大感受野、引入平移不变性

### 4.3.3 特征图尺寸

$$\text{Output} = \left\lfloor \frac{W - K + 2P}{S} \right\rfloor + 1$$

$W$ 输入尺寸，$K$ 卷积核，$P$ 填充，$S$ 步长。

## 4.4 经典 CNN 架构

### LeNet-5 (1998)
- 第一个成功应用的 CNN
- 手写数字识别

### AlexNet (2012)
- ReLU + Dropout + GPU 训练
- ImageNet 冠军，开启深度学习时代

### VGG (2014)
- 全部使用 3×3 卷积
- 16/19 层，深而规律

### GoogLeNet / Inception (2014)
- Inception Module：多尺度并行卷积
- 引入 1×1 卷积降维

### ResNet (2015)
- **残差连接**：$\mathcal{F}(x) + x$
- 解决了"网络退化"问题
- 可训练 100+ 层

$$\text{Output} = \mathcal{F}(x, \{W_i\}) + x$$

### Vision Transformer (ViT, 2020)
- 把图像切成 16×16 patches
- 用 Transformer Encoder 处理

## 4.5 迁移学习

预训练模型 + 微调，是工业界标配：

```python
import torchvision.models as models

# 加载预训练 ResNet
model = models.resnet50(weights=models.ResNet50_Weights.DEFAULT)

# 冻结 backbone
for param in model.parameters():
    param.requires_grad = False

# 替换最后分类层
model.fc = nn.Linear(2048, num_classes)
```

## 4.6 常见 CV 任务

| 任务 | 输出 | 代表模型 |
|---|---|---|
| 图像分类 | 类别 | ResNet, ViT |
| 目标检测 | 边界框 + 类别 | YOLO, Faster R-CNN |
| 语义分割 | 像素级类别 | U-Net, DeepLab |
| 实例分割 | 像素级 + 实例 | Mask R-CNN |
| 人脸识别 | 嵌入向量 | ArcFace, FaceNet |

## 4.7 数据增强

```python
from torchvision import transforms

train_transform = transforms.Compose([
    transforms.RandomResizedCrop(224),
    transforms.RandomHorizontalFlip(),
    transforms.ColorJitter(brightness=0.2, contrast=0.2),
    transforms.ToTensor(),
    transforms.Normalize(mean=[0.485, 0.456, 0.406],
                         std=[0.229, 0.224, 0.225]),
])
```

## 本章小结

- CNN 利用局部性和平移不变性，参数远少于全连接
- ResNet 的残差连接让训练上百层网络成为可能
- 迁移学习让小数据任务也能用大模型

## 思考题

1. 1×1 卷积有什么作用？
2. 为什么 ResNet 的残差连接能缓解梯度消失？
