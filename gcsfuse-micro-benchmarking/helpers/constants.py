default_bs="4KB"
default_file_size="1GB"
default_iodepth=1
default_iotype="read"
default_threads=1
default_nrfiles=1

default_go_version="1.24.0"
default_fio_version="3.39"
default_gcsfuse_version="master"


max_ssh_retries=5
retry_delay=30
poll_interval=1
timeout=5


default_machine_type="n2-standard-16"
default_image_family="debian-11"
default_image_project="debian-cloud"
default_disk_size="10GB"
default_startup_script="./resources/starter_script.sh"
default_delete_after_use=False
default_zone="us-central1-a"
default_region="us-central1"
default_enable_hns=False
default_project="gcs-fuse-test"



default_fio_job_template="./resources/jobfile.fio"
default_mount_config_file=".resources/mount_config.yml"
default_fio_jobcases_file="./resources/fio_job_cases.csv"


default_iterations=5