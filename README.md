
# Explicit Reconstruction and Skeleton-Guided Deformation

This repository contains the official implementation of the code for the paper: **"Explicit Reconstruction and Skeleton-Guided Deformation"** (Please replace with your actual paper title upon publication).

This project provides a comprehensive framework for 3D point cloud processing, focusing on explicit reconstruction and skeleton-guided dynamic deformation.

## 📂 Project Structure

The repository is organized as follows:

```text
.
├── Skeletonization/               # Core logic for skeleton extraction and processing
├── reconstructionAndDeformation/  # Modules for 3D reconstruction and deformation algorithms
├── gaussReconstruction/           # Gaussian-based reconstruction implementation
├── reconstruction.ipynb           # Main notebook for running static reconstruction experiments
├── deformation.ipynb              # Main notebook for running skeleton-guided deformation experiments

```

## 🚀 Getting Started

### Prerequisites

The code is tested on **macOS/Linux** environments. We recommend using **Anaconda** to manage the environment.

1. Clone the repository:
```bash
git clone [https://github.com/Ding-DongDong/Skeleton-Deformation-Project.git](https://github.com/Ding-DongDong/Skeleton-Deformation-Project.git)
cd Skeleton-Deformation-Project

```


2. Install the required dependencies:
*Note: Please ensure you have Python 3.8+ installed.*
```bash
pip install -r requirements.txt
# Or manually install key libraries: numpy, opencv-python, scipy, open3d, jupyter

```



### Running the Code

The core experiments are structured as Jupyter Notebooks for ease of visualization and step-by-step execution.

**1. Static Reconstruction**
To reproduce the reconstruction results (e.g., Chamfer Distance, Normal Consistency), run:

```bash
jupyter notebook reconstruction.ipynb

```

**2. Skeleton-Guided Deformation**
To perform the deformation experiments (including the Skeleton Matching Voting Algorithm and pruning tests), run:

```bash
jupyter notebook deformation.ipynb

```

## 📊 Datasets

The datasets used in this project are available from public repositories. Please download them and place them in the appropriate data directories before running the scripts.

* **Stanford 3D Scanning Repository** (e.g., Bunny, Armadillo):
* [http://graphics.stanford.edu/data/3Dscanrep/](http://graphics.stanford.edu/data/3Dscanrep/)


* **DeformingThings4D Dataset**:
* [http://kaldir.vc.in.tum.de/DeformingThings4D/DeformingThings4D.zip](http://kaldir.vc.in.tum.de/DeformingThings4D/DeformingThings4D.zip)



## 🧩 Algorithm Overview

Key algorithms implemented in this framework include:

* **Skeleton Extraction**: Extracting topological skeletons from point clouds.
* **Skeleton Matching Voting Algorithm**: A robust method to find the matching relationship  between skeletons  and .
* Includes pruning tests based on **Centrality**, **Path Length**, **Topological Consistency**, and **Spatial Configuration**.



## 📝 Citation

If you find this code useful for your research, please cite our paper:

```bibtex
@article{YourName2026Skeleton,
  title={Explicit Reconstruction and Skeleton-Guided Deformation},
  author={Your Name and Co-authors},
  journal={Journal Name},
  year={2026}
}

```

