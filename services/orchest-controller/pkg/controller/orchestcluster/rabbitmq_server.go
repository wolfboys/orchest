package orchestcluster

import (
	orchestv1alpha1 "github.com/orchest/orchest/services/orchest-controller/pkg/apis/orchest/v1alpha1"
	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

func getRabbitMqManifest(hash string, orchest *orchestv1alpha1.OrchestCluster) *appsv1.Deployment {

	matchLabels := getMatchLables(rabbitmq, orchest)
	metadata := getMetadata(rabbitmq, hash, orchest)

	template := corev1.PodTemplateSpec{
		ObjectMeta: metav1.ObjectMeta{
			Labels: matchLabels,
		},
		Spec: corev1.PodSpec{
			Volumes: []corev1.Volume{
				{
					Name: userDirName,
					VolumeSource: corev1.VolumeSource{
						PersistentVolumeClaim: &corev1.PersistentVolumeClaimVolumeSource{
							ClaimName: userDirName,
							ReadOnly:  false,
						},
					},
				},
			},
			Containers: []corev1.Container{
				{
					Name:  rabbitmq,
					Image: orchest.Spec.RabbitMq.Image,
					Ports: []corev1.ContainerPort{
						{
							ContainerPort: 5672,
						},
					},
					VolumeMounts: []corev1.VolumeMount{
						{
							Name:      userDirName,
							MountPath: rabbitmountPath,
							SubPath:   rabbitSubPath,
						},
					},
				},
			},
		},
	}

	deployment := &appsv1.Deployment{
		ObjectMeta: metadata,
		Spec: appsv1.DeploymentSpec{
			Selector: &metav1.LabelSelector{
				MatchLabels: matchLabels,
			},
			Template: template,
			Strategy: appsv1.DeploymentStrategy{
				RollingUpdate: &appsv1.RollingUpdateDeployment{
					MaxUnavailable: &Zero,
				},
			},
		},
	}

	return deployment

}
