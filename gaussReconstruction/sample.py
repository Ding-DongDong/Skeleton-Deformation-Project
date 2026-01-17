import numpy as np

def read_xyz_file(filename):
    """
    读取XYZ格式的点云文件，处理多余的空格和空行
    :param filename: XYZ文件的路径
    :return: 点云数据，形状为 (N, 3) 的numpy数组
    """
    data = []
    with open(filename, 'r') as file:
        for line in file:
            # 去除行首尾的空白字符
            line = line.strip()
            if line:  # 跳过空行
                # 按空格分割，并过滤掉空字符串
                parts = list(filter(None, line.split(' ')))
                if len(parts) == 3:  # 确保每行有3个值
                    data.append([float(parts[0]), float(parts[1]), float(parts[2])])
    return np.array(data)

def random_downsample(point_cloud, target_num_points):
    """
    对点云进行随机下采样
    :param point_cloud: 原始点云数据，形状为 (N, 3) 的numpy数组
    :param target_num_points: 下采样后的目标点数
    :return: 下采样后的点云数据
    """
    num_points = point_cloud.shape[0]
    if target_num_points >= num_points:
        return point_cloud
    
    # 随机选择目标点数的索引
    indices = np.random.choice(num_points, target_num_points, replace=False)
    downsampled_point_cloud = point_cloud[indices]
    
    return downsampled_point_cloud

def save_xyz_file(point_cloud, filename):
    """
    将点云数据保存为XYZ文件
    :param point_cloud: 点云数据，形状为 (N, 3) 的numpy数组
    :param filename: 保存文件的路径
    """
    np.savetxt(filename, point_cloud, delimiter=' ', fmt='%.6f')

def main():
    # 读取XYZ文件
    input_filename = './data/big/basketball_player_vox11_00000001.xyz'
    point_cloud = read_xyz_file(input_filename)
    
    # 计算下采样后的目标点数
    target_num_points = point_cloud.shape[0] // 100
    
    # 进行随机下采样
    downsampled_point_cloud = random_downsample(point_cloud, target_num_points)
    
    # 保存下采样后的点云数据
    output_filename = './data/big/basketball_player_vox11_00000001_sample.xyz'
    save_xyz_file(downsampled_point_cloud, output_filename)
    
    print(f"原始点云点数: {point_cloud.shape[0]}")
    print(f"下采样后点云点数: {downsampled_point_cloud.shape[0]}")
    print(f"下采样后的点云已保存到 {output_filename}")

if __name__ == "__main__":
    main()