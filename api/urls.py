from django.urls import path

from api import views

urlpatterns = [
    path("pulso/", views.pulso, name="pulso"),
    path("comando/", views.comando, name="comando"),
]
