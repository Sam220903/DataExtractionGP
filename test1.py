import numpy as np
import matplotlib.pyplot as plt
import mglearn
from mpl_toolkits.mplot3d import Axes3D
from sklearn.datasets import make_blobs
from sklearn.svm import LinearSVC


def createDataset():
    features, labels = make_blobs(centers=4, random_state=8)
    labels = labels % 2
    return features, labels


def plotOriginalDataset(features, labels):
    plt.figure()
    mglearn.discrete_scatter(features[:, 0], features[:, 1], labels)
    plt.xlabel("Feature 0")
    plt.ylabel("Feature 1")
    plt.show()


def plotLinearSvm(features, labels):
    linearSvm = LinearSVC().fit(features, labels)
    plt.figure()
    mglearn.plots.plot_2d_separator(linearSvm, features)
    mglearn.discrete_scatter(features[:, 0], features[:, 1], labels)
    plt.xlabel("Feature 0")
    plt.ylabel("Feature 1")
    plt.show()


def addSquaredFeature(features):
    # add the squared second feature
    return np.hstack([features, features[:, 1:] ** 2])


def createAxes3d(figure):
    ax = Axes3D(figure, elev=-152, azim=-26)
    figure.add_axes(ax)  # needed in matplotlib >= 3.4
    return ax


def scatter3d(ax, featuresNew, labels):
    # plot first all the points with y == 0, then all with y == 1
    mask = labels == 0
    ax.scatter(featuresNew[mask, 0], featuresNew[mask, 1], featuresNew[mask, 2],
               c='b', cmap=mglearn.cm2, s=60)
    ax.scatter(featuresNew[~mask, 0], featuresNew[~mask, 1], featuresNew[~mask, 2],
               c='r', marker='^', cmap=mglearn.cm2, s=60)


def plot3dDataset(featuresNew, labels):
    figure = plt.figure()
    ax = createAxes3d(figure)
    scatter3d(ax, featuresNew, labels)
    ax.set_xlabel("feature0")
    ax.set_ylabel("feature1")
    ax.set_zlabel("feature1 ** 2")
    plt.show()


def fitLinearSvm3d(featuresNew, labels):
    linearSvm3d = LinearSVC().fit(featuresNew, labels)
    return linearSvm3d


def plot3dDecisionBoundary(linearSvm3d, featuresNew, labels):
    coef = linearSvm3d.coef_.ravel()
    intercept = linearSvm3d.intercept_

    figure = plt.figure()
    ax = createAxes3d(figure)
    xx = np.linspace(featuresNew[:, 0].min() - 2, featuresNew[:, 0].max() + 2, 50)
    yy = np.linspace(featuresNew[:, 1].min() - 2, featuresNew[:, 1].max() + 2, 50)

    XX, YY = np.meshgrid(xx, yy)
    ZZ = (coef[0] * XX + coef[1] * YY + intercept) / -coef[2]
    ax.plot_surface(XX, YY, ZZ, rstride=8, cstride=8, alpha=0.3)
    scatter3d(ax, featuresNew, labels)

    ax.set_xlabel("feature0")
    ax.set_ylabel("feature1")
    ax.set_zlabel("feature0 ** 2")
    plt.show()
    return XX, YY


def plotDecisionBoundaryOriginalFeatures(linearSvm3d, features, labels, XX, YY):
    ZZ = YY ** 2
    dec = linearSvm3d.decision_function(np.c_[XX.ravel(), YY.ravel(), ZZ.ravel()])
    plt.figure()
    plt.contourf(XX, YY, dec.reshape(XX.shape), levels=[dec.min(), 0, dec.max()],
                 cmap=mglearn.cm2, alpha=0.5)
    mglearn.discrete_scatter(features[:, 0], features[:, 1], labels)
    plt.xlabel("Feature 0")
    plt.ylabel("Feature 1")
    plt.show()


def main():
    features, labels = createDataset()
    plotOriginalDataset(features, labels)
    plotLinearSvm(features, labels)

    featuresNew = addSquaredFeature(features)
    plot3dDataset(featuresNew, labels)

    linearSvm3d = fitLinearSvm3d(featuresNew, labels)
    XX, YY = plot3dDecisionBoundary(linearSvm3d, featuresNew, labels)
    plotDecisionBoundaryOriginalFeatures(linearSvm3d, features, labels, XX, YY)


if __name__ == "__main__":
    main()