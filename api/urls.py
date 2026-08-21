from django.urls import path

from api import views

urlpatterns = [
    path("pulso/", views.pulso, name="pulso"),
    path("ingresos/", views.ingresos, name="ingresos"),
    path("comando/", views.comando, name="comando"),
]
